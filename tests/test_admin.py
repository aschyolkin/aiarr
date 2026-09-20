from fastapi.testclient import TestClient

from mae.api import create_app


def test_login_shell_does_not_expose_data_or_key(settings, db, movie):
    job_id = db.submit(movie)
    client = TestClient(create_app(settings, db))
    response = client.get('/admin')
    assert response.status_code == 200
    assert settings.api_key.get_secret_value() not in response.text
    assert movie.title not in response.text
    assert response.headers['cache-control'] == 'no-store'
    assert "frame-ancestors 'none'" in response.headers['content-security-policy']
    for path in ['/health','/admin/jobs',f'/admin/jobs/{job_id}','/admin/profiles','/admin/sources','/metrics']:
        assert client.get(path).status_code == 401
    for action in ['retry','cancel']:
        assert client.post(f'/admin/jobs/{job_id}/{action}').status_code == 401
    assert db.job(job_id)['state'] == 'REQUESTED'
    client.headers['X-Api-Key'] = settings.api_key.get_secret_value()
    assert client.get('/admin/jobs').json()[0]['id'] == job_id
