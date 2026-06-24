import urllib.request
import json

import mimetypes
import uuid

def post_multipart(url, filename):
    boundary = uuid.uuid4().hex
    with open(filename, 'rb') as f:
        file_content = f.read()
    
    body = (
        f"--{boundary}\r\n"
        f"Content-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
        f"Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n"
    ).encode('utf-8') + file_content + f"\r\n--{boundary}--\r\n".encode('utf-8')
    
    req = urllib.request.Request(url, data=body, method='POST')
    req.add_header('Content-Type', f'multipart/form-data; boundary={boundary}')
    res = urllib.request.urlopen(req)
    return json.loads(res.read().decode('utf-8'))

print('Upload:', post_multipart('http://localhost:5001/upload', '../backend/temp_upload.xlsx'))

req = urllib.request.Request('http://localhost:5001/generate?time_limit_seconds=30', data=json.dumps({"classes": [], "branches": [], "days": ["M", "T", "W", "R", "F"]}).encode(), method='POST')
req.add_header('Content-Type', 'application/json')
res = urllib.request.urlopen(req)
data = json.loads(res.read().decode('utf-8'))
print('Status:', data.get('status'))
print('Message:', data.get('message'))
if data.get('bottlenecks'):
    print('Bottlenecks:', data['bottlenecks'])
