"""Explicit live check against the official API, using only synthetic evidence.

Run from the project root with MAE_DEEPSEEK_KEY set. No downloads or media
operations. Uses a disposable SQLite state directory, at most two billed calls.
"""

import argparse
import json
import os
import tempfile
from pathlib import Path

from alembic import command
from alembic.config import Config

from mae.config import Settings
from mae.db import Database
from mae.deepseek import DeepSeek
from mae.domain import Evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-live', action='store_true', required=True)
    parser.parse_args()
    # No environment/config values or exception bodies may be printed.
    os.environ.setdefault('MAE_API_KEY', 'isolated-deepseek-smoke-no-server')
    settings = Settings()
    if not settings.deepseek_key.get_secret_value():
        print(json.dumps({'result':'BLOCKED','reason':'DEEPSEEK_KEY_REQUIRED'}))
        return 2
    with tempfile.TemporaryDirectory(prefix='aiarr-llm-smoke-') as directory:
        old_state = os.environ.get('MAE_STATE_DIR')
        os.environ['MAE_STATE_DIR'] = directory
        db = None
        try:
            command.upgrade(Config('alembic.ini'), 'head')
            settings = settings.model_copy(update={'state_dir':Path(directory),'deepseek_daily_calls':2})
            db = Database(settings.state_dir)
            model = DeepSeek(settings, db)
            evidence = [Evidence(id='synthetic-audio',text='Audio: Russian\nSubtitles: English',method='fixture')]
            answer = model.interpret(evidence)
            if answer is None:
                print(json.dumps({'result':'FAIL','reason':'NO_VALID_INTERPRETATION'}))
                return 1
            assert any(c.field == 'audio_language' and c.value == 'rus' for c in answer.claims)
            before = db.execute('SELECT COUNT(*) n FROM llm_usage')[0]['n']
            assert model.interpret(evidence) == answer
            assert db.execute('SELECT COUNT(*) n FROM llm_usage')[0]['n'] == before
            print(json.dumps({'result':'PASS','model':settings.deepseek_model,'calls':before,'cache_verified':True}))
            return 0
        finally:
            if db is not None:
                db.engine.dispose()
            if old_state is None:
                os.environ.pop('MAE_STATE_DIR',None)
            else:
                os.environ['MAE_STATE_DIR'] = old_state


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({'result':'FAIL','error_type':type(exc).__name__}))
        raise SystemExit(1) from None
