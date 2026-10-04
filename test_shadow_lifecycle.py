"""Shadow safety regressions. Set LM_PROXY_TEST_SHADOW_LIVE=1 for disposable Neo4j fixtures.

Live fixtures use unique project IDs and are removed in teardown. Never reindex a real project.
"""
import asyncio
import importlib.util
import os
from pathlib import Path
import sys
import types
import unittest
import uuid
from unittest import mock

from test_indexing_health_alignment import load_indexing_module, FakeDriver


class CleanupSafetyTests(unittest.TestCase):
    def test_deletion_requires_exact_shadow_selection(self):
        module = load_indexing_module()
        for selection in (None, [], ['real-project']):
            output = asyncio.run(module.cleanup_stale_shadow_graph(False, namespaces=selection))
            self.assertIn('refused', output)

    def test_active_and_unknown_owners_never_reach_delete(self):
        module = load_indexing_module()
        bootstrap = types.ModuleType('graph_bootstrap')
        bootstrap._NEO4J_DB = 'proxy'
        bootstrap.require_driver = mock.AsyncMock(return_value=FakeDriver())
        for owner in ({'status': 'running', 'owner': 'writer', 'heartbeat_at': 0},
                      {'status': 'unknown'}, {'status': 'failed'}):
            with (mock.patch.dict(sys.modules, {'graph_bootstrap': bootstrap}),
                  mock.patch.object(module, '_get_shadow_graph_health', mock.AsyncMock(return_value={})),
                  mock.patch.object(module, '_list_shadow_project_ids', mock.AsyncMock(return_value=[])),
                  mock.patch.object(module, '_inspect_shadow_run', mock.AsyncMock(return_value=owner)),
                  mock.patch.object(module, '_execute_write_scalar', mock.AsyncMock()) as delete):
                output = asyncio.run(module.cleanup_stale_shadow_graph(
                    False, namespaces=['repo::shadow::run']))
                delete.assert_not_awaited()
                self.assertIn('Protected active/unknown namespaces: 1', output)


@unittest.skipUnless(os.getenv('LM_PROXY_TEST_SHADOW_LIVE') == '1', 'explicit live fixture opt-in')
class LiveShadowTests(unittest.TestCase):
    def setUp(self):
        from dotenv import load_dotenv
        from neo4j import GraphDatabase
        load_dotenv(Path(__file__).parent / '.env')
        self.uri = os.getenv('LM_PROXY_NEO4J_URI', 'bolt://127.0.0.1:7687')
        self.user = os.getenv('LM_PROXY_NEO4J_USER', 'neo4j')
        self.password = os.getenv('LM_PROXY_NEO4J_PASSWORD', 'password')
        self.db = os.getenv('LM_PROXY_NEO4J_DB', 'proxy')
        self.driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
        self.project = 'shadow-safety-test-' + uuid.uuid4().hex
        self.run_id = self.project + ':run'
        self.namespace = self.project + '::shadow::' + self.run_id
        self.params = dict(pid=self.project, namespace=self.namespace, run=self.run_id)

    def query(self, text, **params):
        with self.driver.session(database=self.db) as session:
            return session.execute_write(lambda tx: tx.run(text, **self.params, **params).data())

    def tearDown(self):
        self.query('MATCH (n) WHERE n.project_id IN [$pid,$namespace] OR n.namespace=$namespace '
                   'DETACH DELETE n')
        self.driver.close()

    def test_terminal_cleanup_guard_and_expired_running_owner(self):
        from graphrag_core.indexing.shadow import ShadowLifecycle
        # Extract the actual guard without importing the tool module's server dependencies.
        import ast
        tree = ast.parse(Path('tools/hands/indexing.py').read_text())
        guard = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                     and any(isinstance(t, ast.Name) and t.id == '_SHADOW_DELETE_GUARD' for t in n.targets))
        self.query('CREATE (:Node {project_id:$namespace,id:$namespace})')
        def guarded_delete():
            # guard parameter pid refers to the namespace, unlike fixture pid.
            with self.driver.session(database=self.db) as session:
                return session.execute_write(lambda tx: tx.run(
                    guard + ' MATCH (n:Node {project_id:$pid}) DELETE n RETURN count(n) AS deleted',
                    pid=self.namespace).single())['deleted']
        self.assertEqual(guarded_delete(), 0)  # legacy unowned data
        with ShadowLifecycle(self.driver, self.db, self.project, self.namespace, self.run_id):
            self.query('MATCH (s:ShadowRun {namespace:$namespace}) SET s.heartbeat_at=0')
            self.assertEqual(guarded_delete(), 0)  # expiry does not imply a dead writer
        self.query("CREATE (:Project {id:$pid,project_id:$pid,struct_index_status:'in_progress'})")
        self.assertEqual(guarded_delete(), 0)
        self.query("MATCH (p:Project {id:$pid}) SET p.struct_index_status='done'")
        self.assertEqual(guarded_delete(), 1)

    def test_promotion_failure_rolls_back_and_success_replaces_graph(self):
        spec = importlib.util.spec_from_file_location('shadow_struct_runner', 'scripts/run_struct_index.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.query('CREATE (:Node:File {project_id:$pid,id:$pid,name:"old"}) '
                   'CREATE (:Node:File {project_id:$namespace,id:$namespace,stable_id:$namespace, '
                   'last_seen_run:$run,name:"new"})')
        def promote():
            module._promote_struct_shadow_graph(self.uri, self.user, self.password, self.db,
                                               self.project, self.namespace, self.run_id)
        with self.assertRaisesRegex(RuntimeError, 'invariant failed'):
            promote()
        rows = self.query('MATCH (n:File) WHERE n.project_id IN [$pid,$namespace] '
                          'RETURN n.name AS name,n.project_id AS project ORDER BY name')
        self.assertEqual(len(rows), 2)
        self.assertEqual(next(r['project'] for r in rows if r['name']=='old'), self.project)
        self.query('MATCH (n:File {project_id:$namespace}) SET n.stable_id=$pid + ":new"')
        promote()
        rows = self.query('MATCH (n:File) WHERE n.project_id IN [$pid,$namespace] RETURN n.name AS name')
        self.assertEqual(rows, [{'name': 'new'}])

    def test_finalization_failure_exits_nonzero(self):
        import tempfile
        spec = importlib.util.spec_from_file_location('shadow_failure_runner', 'scripts/run_struct_index.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.NamedTemporaryFile() as manifest:
            argv = ['run_struct_index.py', '/tmp', self.project, '--manifest-file', manifest.name,
                    '--neo4j-db', self.db]
            with (mock.patch.object(sys, 'argv', argv),
                  mock.patch.object(module.ts_pack, 'index_workspace', return_value=[]),
                  mock.patch.object(module.ts_pack, 'finalize_struct_graph',
                                    side_effect=RuntimeError('fixture finalizer failure')),
                  mock.patch.object(module, '_set_struct_run_status') as status):
                self.assertEqual(module.main(), 1)
                self.assertEqual(status.call_args.args[5], 'finalize_failed')
        rows = self.query('MATCH (s:ShadowRun {project_id:$pid}) RETURN s.status AS status')
        self.assertEqual(rows, [{'status': 'failed'}])

    def test_writer_exception_records_terminal_failure(self):
        from graphrag_core.indexing.shadow import ShadowLifecycle
        with self.assertRaisesRegex(RuntimeError, 'fixture failure'):
            with ShadowLifecycle(self.driver, self.db, self.project, self.namespace, self.run_id):
                raise RuntimeError('fixture failure')
        row = self.query('MATCH (s:ShadowRun {namespace:$namespace}) '
                         'RETURN s.status AS status,s.finished_at AS finished')[0]
        self.assertEqual(row['status'], 'failed')
        self.assertIsInstance(row['finished'], int)


if __name__ == '__main__':
    unittest.main()
