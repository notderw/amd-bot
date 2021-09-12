import os
import time
import logging
import asyncio
import traceback

from datetime import datetime, timedelta
from typing import List

import asyncpraw
import yaml

from pydantic import BaseModel
from derw import makeLogger

# if we don't set this, praw utc timestamps take a shit
os.environ['TZ'] = 'UTC'
time.tzset()


SUBREDDIT = os.environ.get('SUBREDDIT')


log = makeLogger(__name__)
log.setLevel(logging.DEBUG)


class Config(BaseModel):
    time_until_message: int
    time_until_remove: int

    add_flair_subject: str
    add_flair_message: str

    tech_support_flair: str
    tech_support_rr: str

    battlestation_flairs: List[str]
    battlestation_rr: str


class AMDBot:
    def __init__(self) -> None:
        self.reddit = asyncpraw.Reddit('AMD', user_agent='/r/AMD bot by /u/RenegadeAI') # scopes: identity, modcontributors (for subreddit.mod.removal_reasons?!?!?), modposts, privatemessages, read, submit, wikiread

    async def _load_config(self) -> None:
        page = await self.subreddit.wiki.get_page('amd-bot')
        self.config = Config(**yaml.safe_load(page.content_md))
        log.debug(f'Loaded config for {self.subreddit.display_name}')

    async def comment_and_remove(self, submission, reason_id) -> None:
        removal_reason = await self.subreddit.mod.removal_reasons.get_reason(reason_id)
        comment_message = removal_reason.message

        comment = await submission.reply(comment_message)
        await comment.mod.distinguish(sticky=True)

        await submission.mod.remove(reason_id=reason_id)
        await submission.mod.lock()

    async def is_ts(self, submission) -> bool:
        if not hasattr(submission, 'link_flair_template_id'):
            return False

        if submission.link_flair_template_id != self.config.tech_support_flair:
            return False

        if submission.approved_by:
            return False


        await self.subreddit.load()

        moderators = await self.subreddit.moderators()
        if submission.author in moderators:
            return False

        await self.comment_and_remove(submission, self.config.tech_support_rr)

        log.info(f'{submission.shortlink} - Removed tech support')

        return True

    async def handler(self, submission) -> None:
        try:
            await submission.load()

            log.debug(f'{submission.shortlink} - [{submission.link_flair_text}] {submission.title}')

            if await self.is_ts(submission):
                return

            if not submission.link_flair_text:
                log.info(f'{submission.shortlink} - Does not have flair')

                sleep_time_until_message = self.config.time_until_message - (datetime.utcnow() - datetime.fromtimestamp(submission.created_utc)).total_seconds()
                if sleep_time_until_message > 0:
                    await asyncio.sleep(sleep_time_until_message)

                add_flair_message = self.config.add_flair_message.format(post_url=submission.shortlink, time_until_remove=timedelta(seconds=self.config.time_until_remove)).strip()

                sent_messages = [m async for m in self.reddit.inbox.sent() if m.body == add_flair_message]
                if sent_messages:
                    log.debug(f'{submission.shortlink} - Already sent message')

                    sleep_time_until_remove = self.config.time_until_remove - (datetime.utcnow() - datetime.fromtimestamp(sent_messages[0].created_utc)).total_seconds()

                else:
                    await submission.author.message(self.config.add_flair_subject, add_flair_message)
                    log.debug(f'{submission.shortlink} - Sent message')

                    sleep_time_until_remove = self.config.time_until_remove - (datetime.utcnow() - datetime.fromtimestamp(submission.created_utc)).total_seconds()


                if sleep_time_until_remove > 0:
                    await asyncio.sleep(sleep_time_until_remove)

                submission = await self.reddit.submission(submission.id)

                if await self.is_ts(submission):
                    return

                if not submission.link_flair_text:
                    await submission.mod.remove()
                    return


            if submission.link_flair_template_id in self.config.battlestation_flairs:
                # if posted day is not sat / sun
                if not 5 <= datetime.fromtimestamp(submission.created_utc).weekday() <= 6:
                    await self.comment_and_remove(submission, self.config.battlestation_rr)
                    log.info(f'{submission.shortlink} - Removed battlestation')

        except Exception as e:
            log.critical(traceback.format_exc())
            log.critical(e)


    async def run(self) -> None:
        self.me = me = await self.reddit.user.me()
        log.info(f'Logged in as {me.name} - {me.id}')

        self.subreddit = subreddit = await self.reddit.subreddit(SUBREDDIT)
        await self.subreddit.load()

        log.info(f'Watching /r/{subreddit.display_name}')

        await self._load_config()

        # async for r in self.subreddit.mod.removal_reasons:
        #     print(f'{r.id} - {r.title}')
        # return

        async for sub in subreddit.stream.submissions(skip_existing=True):
            asyncio.create_task(self.handler(sub))

    async def close(self) -> None:
        await self.reddit.close()


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    bot = AMDBot()

    try:
        loop.run_until_complete(bot.run())

    except KeyboardInterrupt:
        pass

    finally:
        loop.run_until_complete(bot.close())
        loop.close()
