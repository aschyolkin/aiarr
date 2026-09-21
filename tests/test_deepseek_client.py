import json

import pytest
from pydantic import SecretStr, ValidationError

from mae.config import Settings
from mae.deepseek import CLAIM_VALUES, DeepSeek
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
    assert json.dumps(CLAIM_VALUES) in request['messages'][0]['content']
    assert 'private-value' not in route.calls[0].request.content.decode()
    assert db.execute('SELECT SUM(tokens) n FROM llm_usage')[0]['n'] == 27


@pytest.mark.parametrize('response', [None, [], 'invalid', {'usage': None}, {'usage': []},
    {'choices':None}, {'choices':[]}, {'choices':[{'message':{'content':'bad json'}}]}])
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


def test_rejected_semantic_response_still_counts_tokens(settings, db, respx_mock):
    settings.deepseek_key = SecretStr('test-secret-not-real')
    respx_mock.post('https://api.deepseek.com/chat/completions').respond(200, json={
        'choices': [{'message': {'content': json.dumps({
            'claims': [{'field': 'audio_language', 'value': 'Russian',
                        'evidence_id': 'one', 'quote': 'Audio: Russian'}],
            'unknown': [], 'contradictions': [],
        })}}],
        'usage': {'total_tokens': 42},
    })
    assert DeepSeek(settings, db).interpret([
        Evidence(id='one', text='Audio: Russian', method='fixture')
    ]) is None
    assert db.execute('SELECT SUM(tokens) n FROM llm_usage')[0]['n'] == 84


@pytest.mark.parametrize('model',['deepseek-v4-flash','deepseek-v4-pro','another-provider'])
def test_alternate_models_are_rejected(settings, model):
    with pytest.raises(ValidationError):
        Settings(api_key=settings.api_key,deepseek_model=model,_env_file=None)


def test_request_budget_persists_across_clients_and_evidence(settings, db, respx_mock):
    settings.deepseek_key = SecretStr('test-secret-not-real')
    settings.deepseek_request_calls = 1
    route = respx_mock.post('https://api.deepseek.com/chat/completions').respond(200, json=[])
    assert DeepSeek(settings, db).interpret([], request_key='job:1:generation:1') is None
    assert DeepSeek(settings, db).interpret([
        Evidence(id='new', text='Audio: unknown', method='fixture')
    ], request_key='job:1:generation:1') is None
    assert route.call_count == 1
    row = db.execute('SELECT * FROM llm_usage')[0]
    assert row['charged_tokens'] > 0 and row['outcome'] == 'INVALID_RESPONSE'
    assert row['latency_ms'] >= 0


@pytest.mark.parametrize('setting', ['deepseek_daily_tokens', 'deepseek_request_tokens'])
def test_token_budget_prevents_admission(settings, db, respx_mock, setting):
    settings.deepseek_key = SecretStr('test-secret-not-real')
    setattr(settings, setting, 1)
    assert DeepSeek(settings, db).interpret([]) is None
    assert not respx_mock.calls
    assert db.execute('SELECT * FROM llm_usage') == []


@pytest.mark.parametrize('status', [400, 401, 402, 403, 404, 429])
def test_permanent_or_rate_limit_errors_do_not_retry_immediately(settings, db, respx_mock, status):
    settings.deepseek_key = SecretStr('test-secret-not-real')
    route = respx_mock.post('https://api.deepseek.com/chat/completions').respond(status)
    assert DeepSeek(settings, db).interpret([]) is None
    assert route.call_count == 1
    row = db.execute('SELECT * FROM llm_usage')[0]
    assert row['outcome'] == f'HTTP_{status}'
    assert row['charged_tokens'] > 0


def test_concurrent_budget_reservations_are_atomic(settings, db):
    from concurrent.futures import ThreadPoolExecutor

    settings.deepseek_daily_calls = 1
    def reserve(_):
        return DeepSeek(settings, db).reserve('same-request', 100)
    with ThreadPoolExecutor(max_workers=4) as executor:
        reservations = list(executor.map(reserve, range(4)))
    assert sum(value is not None for value in reservations) == 1
    assert len(db.execute('SELECT * FROM llm_usage')) == 1
