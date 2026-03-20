import os
os.environ['DATABASE_URL']='sqlite:////mnt/data/gitgud-5.1.2/test_web.db'
os.environ['ADMIN_EMAIL']='admin@local'
os.environ['ADMIN_PASSWORD']='admin123'
os.environ.pop('BOT_TOKEN', None)
import app

client = app.app.test_client()

# login page
r = client.get('/login')
print('GET /login', r.status_code)

# do login
r = client.post('/login', data={'email':'admin@local','password':'admin123'}, follow_redirects=False)
print('POST /login', r.status_code, 'Location', r.headers.get('Location'))

# follow to dashboard
if r.status_code in (301,302) and r.headers.get('Location'):
    r2 = client.get(r.headers['Location'])
    print('GET dashboard', r2.status_code)

# logs endpoint requires login, we have session cookie? use follow_redirects
r = client.post('/login', data={'email':'admin@local','password':'admin123'}, follow_redirects=True)
print('login follow', r.status_code)

r = client.get('/logs?limit=5')
print('GET /logs', r.status_code, r.json)

# workload pages should load even empty
r = client.get('/')
print('GET /', r.status_code)

