"""Disposable parser retention probe; synthetic input, no network or external services."""
from __future__ import annotations
import argparse, gc, hashlib, importlib.metadata, inspect, json, os
from pathlib import Path
import shutil, statistics, sys, tempfile, threading, time, tracemalloc

SOURCES = {
 'python': 'import math\nclass Sample:\n    def value(self, x):\n        return math.floor(x)\ndef answer(x):\n    return Sample().value(x)\n# MARKER\n',
 'typescript': 'import {join} from "path";\nexport class Sample { value(x: number): number { return x + 1; } }\nexport function answer(x: number) { return new Sample().value(x); }\n// MARKER\n',
 'swift': 'import Foundation\nstruct Sample {\n    func value(_ x: Int) -> Int { return x + 1 }\n}\nfunc answer(_ x: Int) -> Int { return Sample().value(x) }\n// MARKER\n',
}

def main():
 p=argparse.ArgumentParser();p.add_argument('--mode',required=True);p.add_argument('--calls',type=int,default=12000);p.add_argument('--output',required=True);a=p.parse_args()
 import psutil
 import tree_sitter_language_pack as ts
 proc=psutil.Process()
 initial_cache=Path(ts.cache_dir()); assets={}
 with tempfile.TemporaryDirectory(prefix='parser-retention-') as directory:
  base=Path(directory).resolve()/'cache'; base.mkdir()
  ts.configure(cache_dir=str(base))
  libs=Path(ts.cache_dir()).resolve(); assert libs.is_relative_to(base); libs.mkdir(parents=True,exist_ok=True)
  for lang in SOURCES:
   source=initial_cache/f'libtree_sitter_{lang}.dylib';data=source.read_bytes()
   (libs/source.name).write_bytes(data);assets[lang]=hashlib.sha256(data).hexdigest()
  configs={lang:ts.ProcessConfig(lang,chunk_max_size=256) for lang in SOURCES}
  for lang in SOURCES: ts.get_parser(lang)
  # No download is needed or requested: only the three copied native assets are used.
  sig=inspect.signature(ts.build_semantic_payload);overlap='chunk_overlap' if 'chunk_overlap' in sig.parameters else '_chunk_overlap'
  langs=list(SOURCES)
  query_tree=ts.parse_string('python',SOURCES['python']) if a.mode.startswith('query_') else None
  def operation(index):
   if a.mode.startswith('query_'):
    pattern='(identifier) @symbol' if a.mode=='query_fixed' else f'(identifier) @symbol_{index:08d}'
    return query_tree.run_query('python',pattern)
   lang=langs[index%len(langs)];source=SOURCES[lang]
   if a.mode!='process_fixed':source=source.replace('MARKER',f'probe_{index:08d}')
   if a.mode.startswith('process') or a.mode=='threads':return ts.process(source,configs[lang])
   if a.mode=='facts':return ts.extract_file_facts(source,lang,f'fixture_{index:08d}.txt')
   if a.mode=='payload':return ts.build_semantic_payload(source,lang,f'fixture_{index:08d}.txt','synthetic-audit',chunk_id_version='v1',chunk_max_size=256,**{overlap:32})
   if a.mode=='bindings':
    parser=ts.get_parser(lang);tree=parser.parse(source.encode());return tree.root_node.child_count
   raise ValueError('unknown mode')
  tracemalloc.start(1)
  for i in range(600): operation(i)
  samples=[];started=time.monotonic()
  def sample(calls):
   gc.collect();current,peak=tracemalloc.get_traced_memory()
   s={'calls':calls,'rss':proc.memory_info().rss,'python_current':current,'python_peak':peak,'fds':proc.num_fds(),'threads':proc.num_threads()}
   samples.append(s)
  sample(0)
  if a.mode=='threads':
   failures=[]
   def worker(offset):
    try:
     for j in range(20):operation(offset+j)
    except Exception as error:failures.append(type(error).__name__)
   for batch in range(100):
    threads=[threading.Thread(target=worker,args=(batch*80+n*20,)) for n in range(4)]
    for t in threads:t.start()
    for t in threads:t.join()
    del threads
    if (batch+1)%5==0:sample((batch+1)*80)
   if failures:raise RuntimeError(str(failures))
  else:
   for i in range(a.calls):
    operation(i+600)
    if (i+1)%(100 if a.mode.startswith('query_') else 600)==0:
     sample(i+1)
     if samples[-1]['rss']-samples[0]['rss']>256*1024*1024:raise RuntimeError('Probe memory safety ceiling reached')
  steady=samples[5:]
  def median(rows,key):return statistics.median(row[key] for row in rows)
  summary={'rss_start':samples[0]['rss'],'rss_end':samples[-1]['rss'],'rss_delta':samples[-1]['rss']-samples[0]['rss'],'steady_rss_median_delta':median(steady[-5:],'rss')-median(steady[:5],'rss'),'python_delta':samples[-1]['python_current']-samples[0]['python_current'],'fds_start':samples[0]['fds'],'fds_end':samples[-1]['fds'],'threads_start':samples[0]['threads'],'threads_end':samples[-1]['threads']}
  d={'mode':a.mode,'warmup_calls':600,'samples':samples,'summary':summary,'seconds':time.monotonic()-started,'python':sys.version,'distribution_version':importlib.metadata.version('tree-sitter-language-pack'),'asset_sha256':assets,'limits':['RSS includes allocator caching; not a native allocation proof.','Current process RSS, not peak RSS, measured after Python GC.','No USS or native allocation stack instrumentation.','Only three cached languages and bounded synthetic inputs.']}
  Path(a.output).write_text(json.dumps(d,indent=2)+'\n');print(json.dumps({'mode':a.mode,'summary':summary,'seconds':round(d['seconds'],2)}),flush=True)

if __name__=='__main__':main()
