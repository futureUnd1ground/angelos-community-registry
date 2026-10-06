#!/usr/bin/env python3
"""Fail before publication if registry JSON or its entry structure is invalid."""
import json, re, sys
from pathlib import Path
from urllib.parse import urlparse


def validate(path):
    try:
        payload=json.loads(Path(path).read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        raise ValueError('{}: line {}, column {}: {}'.format(path,exc.lineno,exc.colno,exc.msg)) from None
    if not isinstance(payload,dict) or not isinstance(payload.get('plugins'),list):
        raise ValueError('Registry must contain a plugins array')
    ids=set()
    for index,entry in enumerate(payload['plugins']):
        if not isinstance(entry,dict):raise ValueError('Plugin #{} must be an object'.format(index+1))
        plugin_id=entry.get('id','')
        if not isinstance(plugin_id,str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]*',plugin_id):raise ValueError('Invalid plugin ID at #{}'.format(index+1))
        if plugin_id in ids:raise ValueError('Duplicate plugin ID: '+plugin_id)
        ids.add(plugin_id)
        for key in ('name','version','source','repository'):
            if not isinstance(entry.get(key),str) or not entry[key].strip():raise ValueError('{}: missing {}'.format(plugin_id,key))
        for key in ('source','repository'):
            url=urlparse(entry[key])
            if url.scheme!='https' or not url.hostname:raise ValueError('{}: {} must use HTTPS'.format(plugin_id,key))
        for key in ('tags','dependencies','permissions'):
            values=entry.get(key,[])
            if not isinstance(values,list) or any(not isinstance(v,str) for v in values):raise ValueError('{}: {} must contain only strings'.format(plugin_id,key))
        if entry.get('status','pending') not in ('pending','approved','rejected'):raise ValueError(plugin_id+': invalid status')
    return len(ids)


if __name__=='__main__':
    try:print('Valid registry: {} plugins'.format(validate(sys.argv[1] if len(sys.argv)>1 else 'plugins.json')))
    except (OSError,ValueError) as exc:
        print(str(exc),file=sys.stderr);sys.exit(1)
