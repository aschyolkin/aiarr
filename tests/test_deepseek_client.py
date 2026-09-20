import json

import pytest
from pydantic import SecretStr, ValidationError

from mae.config import Settings
from mae.deepseek import DeepSeek
from mae.domain import Evidence


def test_flash_request_cache_and_no_secret_evidence(settings, db, respx_mock):
    settings.deepseek_key = SecretStr('test-secret-not-real')
    route = respx_mock.post('https://api.deepseek.com/chat/completions').respond(200, json={
        'choices':[{'message':{'content':json.dumps({'claims':[], 'unknown':['audio_language'], 'contradictions':[]})}}],
        'usage':{'total_tokens':27},
    })
    evidence = [Evidence(id='one',text='Audio: unspecified\npasskey=private-value',method='fixture')]
    model = DeepSeek(settings,db)
    assert model.interpret(evidence).unknown == ['audio_language']
    assert model.interpret(evidence).unknown == ['audio_language']
    assert route.call_count == 1
    request = json.loads(route.calls[0].request.content)
    assert request['model'] == 'deepseek-flash'
    assert request['thinking'] == {'type':'disabled'}
    assert request['response_format'] == {'type':'json_object'}
    assert 'tools' not in request
    assert 'private-value' not in route.calls[0].request.content.decode()
    assert db.execute('SELECT SUM(tokens) n FROM llm_usage')[0]['n'] == 27


@pytest.mark.parametrize('response', [{'choices':None}, {'choices':[]}, {'choices':[{'message':{'content':'bad json'}}]}])
def test_malformed_response_abstains_after_two_attempts(settings, db, respx_mock, response):
    settings.deepseek_key = SecretStr('test-secret-not-real')
    route = respx_mock.post('https://api.deepseek.com/chat/completions').respond(200,json=response)
    assert DeepSeek(settings,db).interpret([Evidence(id='one',text='Audio: unspecified',method='fixture')]) is None
    assert route.call_count == 2
    assert all(json.loads(call.request.content)['model'] == 'deepseek-flash' for call in route.calls)


def test_call_budget_prevents_network(settings, db, respx_mock):
    settings.deepseek_key = SecretStr('test-secret-not-real')
    settings.deepseek_daily_calls = 0
    assert DeepSeek(settings,db).interpret([]) is None
    assert not respx_mock.calls


@pytest.mark.parametrize('model',['deepseek-v4-flash','deepseek-v4-pro','another-provider'])
def test_alternate_models_are_rejected(settings, model):
    with pytest.raises(ValidationError):
        Settings(api_key=settings.api_key,deepseek_model=model,_env_file=None)
