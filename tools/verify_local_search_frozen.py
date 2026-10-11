"""发布前验证打包后的多进程、动态加速组件和 sqlite-vec。"""
import multiprocessing
import argparse
import json
from pathlib import Path

def main():
    import sqlite3,sqlite_vec
    from wechat_decrypt_tool.local_search.inference import LocalInference
    from wechat_decrypt_tool.local_search.catalog import model_spec,model_dir
    from wechat_decrypt_tool.local_search.gpu import MANIFEST_PATH,DML_MANIFEST_PATH
    from wechat_decrypt_tool.gpu_devices import enumerate_devices
    import sys
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path)
    parser.add_argument('--backend',choices=('cuda','dml'),default='cuda')
    parser.add_argument('--identity')
    parser.add_argument('--component-root',type=Path)
    args=parser.parse_args()
    root=args.root.resolve()
    spec=model_spec('bge-small-zh')
    manifest=json.loads((MANIFEST_PATH if args.backend=='cuda' else DML_MANIFEST_PATH).read_text(encoding='utf-8'))
    gpu=args.component_root or root/'local_search_gpu'/manifest['id']/f'cp{sys.version_info.major}{sys.version_info.minor}'
    candidates=[device for device in enumerate_devices() if device['eligible']
                and device['vendor']==('nvidia' if args.backend=='cuda' else 'amd')
                and (not args.identity or device['identity'].casefold()==args.identity.casefold())]
    if len(candidates)!=1:
        raise RuntimeError('必须用 --identity 指定唯一可用设备')
    identity=candidates[0]['identity']
    engine=LocalInference(gpu_root=gpu if args.backend=='cuda' else None,dml_root=gpu if args.backend=='dml' else None)
    try:
        for device in ['cpu',args.backend]:
            vector=engine.encode(model_dir(root/'models',spec['id']),spec,['本地打包检索验证'],device,
                                 device_identity=identity if device!='cpu' else None)
            print(json.dumps({'requested':device,**engine.status,'dimension':len(vector[0])},ensure_ascii=False),flush=True)
            assert engine.status['actual_device']==device
        db=sqlite3.connect(':memory:');db.enable_load_extension(True);sqlite_vec.load(db)
        print('sqlite_vec='+db.execute('select vec_version()').fetchone()[0],flush=True)
    finally:engine.close()

if __name__=='__main__':
    multiprocessing.freeze_support()
    main()
