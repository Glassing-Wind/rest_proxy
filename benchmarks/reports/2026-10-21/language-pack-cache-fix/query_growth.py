"""Contrast new and replayed native query patterns in one disposable process."""
import gc, hashlib, json
from pathlib import Path
import psutil, shutil, tempfile
import tree_sitter_language_pack as ts
import argparse
p=argparse.ArgumentParser();p.add_argument('--output',required=True);args=p.parse_args()
proc=psutil.Process();original=Path(ts.cache_dir());rows=[]
with tempfile.TemporaryDirectory(prefix='query-cache-probe-') as directory:
 base=Path(directory).resolve()/'cache';base.mkdir();ts.configure(cache_dir=str(base));cache=Path(ts.cache_dir());cache.mkdir(parents=True,exist_ok=True)
 shutil.copy2(original/'libtree_sitter_python.dylib',cache/'libtree_sitter_python.dylib')
 tree=ts.parse_string('python','x = 1\n')
 for _ in range(200):tree.run_query('python','(identifier) @warmup')
 def sample(phase,unique):
  gc.collect();rows.append({'phase':phase,'unique_patterns':unique,'rss':proc.memory_info().rss,'fds':proc.num_fds(),'threads':proc.num_threads()})
 sample('warmup',0)
 for phase,start,end,unique in [('new_3000',0,3000,3000),('replay_3000',0,3000,3000),('new_3000_more',3000,6000,6000),('replay_6000',0,6000,6000)]:
  for index in range(start,end):tree.run_query('python',f'(identifier) @symbol_{index:08d}')
  sample(phase,unique)
 del tree;sample('tree_dropped',6000)
Path(args.output).write_text(json.dumps({'samples':rows,'scope':'synthetic patterns on one Python tree; isolated cache; results dropped each call'},indent=2)+'\n')
print(json.dumps(rows))
