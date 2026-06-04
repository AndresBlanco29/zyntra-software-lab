#!/usr/bin/env python
import time, http.client, sys

host = '127.0.0.1'
port = 8000
path = '/quickbooks/'

for i in range(20):
    try:
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request('GET', path)
        r = conn.getresponse()
        print('STATUS', r.status)
        print('CONTENT_TYPE', r.getheader('Content-Type'))
        body = r.read(5000).decode('utf-8', errors='replace')
        print(body[:5000])
        sys.exit(0)
    except Exception as e:
        print(f'retry {i} - {e}')
        time.sleep(1)
print('FAILED')
sys.exit(2)
