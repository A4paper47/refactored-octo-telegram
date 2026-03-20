import os, asyncio
os.environ['DATABASE_URL'] = 'sqlite:////mnt/data/gitgud-5.1.2/test_srt.db'
# ensure bot disabled for this test
os.environ.pop('BOT_TOKEN', None)

import app as webapp
from db import db
import bot_ptb
from datetime import datetime
from telegram.constants import ChatType

# Patch bot settings to avoid external calls
bot_ptb.SRT_OUTBOX_CHAT_ID = None
bot_ptb.ADMIN_TELEGRAM_CHAT_ID = None

class FakeBot:
    async def send_message(self, *args, **kwargs):
        return None
    async def send_document(self, *args, **kwargs):
        return None

class FakeContext:
    def __init__(self):
        self.bot = FakeBot()

class FakeDocument:
    def __init__(self, file_name):
        self.file_name = file_name
        self.file_id = 'FILEID'
        self.file_unique_id = 'UNIQ'
        self.file_size = 123
        self.mime_type = 'application/x-subrip'

class FakeMessage:
    def __init__(self, file_name, caption=None, message_id=101):
        self.document = FakeDocument(file_name)
        self.caption = caption
        self.message_id = message_id
        self.date = datetime.utcnow()
        self.replies = []
    async def reply_text(self, text, parse_mode=None):
        self.replies.append((text, parse_mode))
        return None

class FakeUser:
    def __init__(self, uid=555, username='tester', full_name='Test User'):
        self.id = uid
        self.username = username
        self.full_name = full_name

class FakeChat:
    def __init__(self, cid=777):
        self.id = cid
        self.type = ChatType.PRIVATE

class FakeUpdate:
    def __init__(self, msg, user, chat):
        self.effective_message = msg
        self.effective_user = user
        self.effective_chat = chat

async def run_case(file_name, caption=None):
    msg = FakeMessage(file_name, caption=caption)
    upd = FakeUpdate(msg, FakeUser(), FakeChat())
    ctx = FakeContext()
    await bot_ptb.on_dm_srt_auto_forward(upd, ctx)
    return msg

with webapp.app.app_context():
    # ensure tables exist
    db.create_all()
    webapp.auto_migrate_schema()

    # add translator row with tg_user_id = 555
    from models import Translator, TranslationTask

    tr = Translator.query.filter_by(name='TestTranslator').first()
    if not tr:
        tr = Translator(name='TestTranslator', tg_user_id=555, tg_username='tester')
        db.session.add(tr)
        db.session.commit()

    # create a SENT translation task for code BN-260303-01
    t = TranslationTask(movie_code='BN-260303-01', title='BN-260303-01', year=None, lang='bn', translator_id=tr.id, translator_name=tr.name, status='SENT')
    db.session.add(t)
    db.session.commit()

    # Run handler
    msg = asyncio.run(run_case('BN-260303-01.srt'))

    # Fetch logs
    rows = db.session.execute(webapp.sql_text('SELECT level, source, message, traceback FROM system_logs ORDER BY id DESC LIMIT 1')).fetchall()
    print('replies:', msg.replies)
    print('log_row:', rows[0][0], rows[0][1])
    print('message_preview:', rows[0][2].split('\n')[0:5])
    print('traceback_has_json:', rows[0][3].lstrip().startswith('{'))

    # Check task completed
    t2 = TranslationTask.query.get(t.id)
    print('task_status:', t2.status)
