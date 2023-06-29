#!/usr/bin/env python3
from pyrogram.handlers import MessageHandler
from pyrogram.filters import command
from base64 import b64encode
from re import match as re_match
from asyncio import sleep
from aiofiles.os import path as aiopath

from bot import bot, DOWNLOAD_DIR, LOGGER, config_dict
from bot.helper.ext_utils.bot_utils import is_url, is_magnet, is_mega_link, is_gdrive_link, get_content_type, new_task, sync_to_async, is_rclone_path, is_telegram_link, arg_parser
from bot.helper.ext_utils.exceptions import DirectDownloadLinkException
from bot.helper.ext_utils.task_manager import task_utils
from bot.helper.mirror_utils.download_utils.aria2_download import add_aria2c_download
from bot.helper.mirror_utils.download_utils.gd_download import add_gd_download
from bot.helper.mirror_utils.download_utils.qbit_download import add_qb_torrent
from bot.helper.mirror_utils.download_utils.mega_download import add_mega_download
from bot.helper.mirror_utils.download_utils.rclone_download import add_rclone_download
from bot.helper.mirror_utils.rclone_utils.list import RcloneList
from bot.helper.mirror_utils.download_utils.direct_link_generator import direct_link_generator
from bot.helper.mirror_utils.download_utils.telegram_download import TelegramDownloadHelper
from bot.helper.telegram_helper.bot_commands import BotCommands
from bot.helper.telegram_helper.filters import CustomFilters
from bot.helper.telegram_helper.message_utils import sendMessage, editMessage, get_tg_link_content, delete_links, auto_delete_message
from bot.helper.listeners.tasks_listener import MirrorLeechListener
from bot.helper.ext_utils.help_messages import MIRROR_HELP_MESSAGE
from bot.helper.ext_utils.bulk_links import extract_bulk_links


@new_task
async def _mirror_leech(client, message, isQbit=False, isLeech=False, sameDir=None, bulk=[]):
    text = message.text.split('\n')
    input_list = text[0].split(' ')

    arg_base = {'link': '', '-i': 0, '-m': '', '-d': False, '-j': False, '-s': False, '-b': False,
                '-n': '', '-e': False, '-z': False, '-up': '', '-rcf': '', '-au': '', '-ap': ''}

    args = arg_parser(input_list[1:], arg_base)

    try:
        multi = int(args['-i'])
    except:
        multi = 0

    select = args['-s']
    seed = args['-d']
    isBulk = args['-b']
    folder_name = args['-m']
    name = args['-n']
    up = args['-up']
    rcf = args['-rcf']
    link = args['link']
    compress = args['-z']
    extract = args['-e']
    join = args['-j']

    bulk_start = 0
    bulk_end = 0
    ratio = None
    seed_time = None
    reply_to = None
    file_ = None
    session = ''
    if not isinstance(seed, bool):
        dargs = seed.split(':')
        ratio = dargs[0] or None
        if len(dargs) == 2:
            seed_time = dargs[1] or None
        seed = True

    if not isinstance(isBulk, bool):
        dargs = isBulk.split(':')
        bulk_start = dargs[0] or None
        if len(dargs) == 2:
            bulk_end = dargs[1] or None
        isBulk = True

    if folder_name and not isBulk:
        seed = False
        ratio = None
        seed_time = None
        folder_name = f'/{folder_name}'
        if sameDir is None:
            sameDir = {'total': multi, 'tasks': set(), 'name': folder_name}
        sameDir['tasks'].add(message.id)

    if isBulk:
        try:
            bulk = await extract_bulk_links(message, bulk_start, bulk_end)
            if len(bulk) == 0:
                raise ValueError('Bulk Empty!')
        except:
            await sendMessage(message, 'Reply to text file or tg message that have links seperated by new line!')
            return
        b_msg = input_list[:1]
        b_msg.append(f'{bulk[0]} -i {len(bulk)}')
        nextmsg = await sendMessage(message, " ".join(b_msg))
        nextmsg = await client.get_messages(chat_id=message.chat.id, message_ids=nextmsg.id)
        nextmsg.from_user = message.from_user
        _mirror_leech(client, nextmsg, isQbit, isLeech, sameDir, bulk)
        return

    if len(bulk) != 0:
        del bulk[0]

    @new_task
    async def __run_multi():
        if multi <= 1:
            return
        await sleep(5)
        if len(bulk) != 0:
            msg = input_list[:1]
            msg.append(f'{bulk[0]} -i {multi - 1}')
            nextmsg = await sendMessage(message, " ".join(msg))
        else:
            msg = [s.strip() for s in input_list]
            index = msg.index('-i')
            msg[index+1] = f"{multi - 1}"
            nextmsg = await client.get_messages(chat_id=message.chat.id, message_ids=message.reply_to_message_id + 1)
            nextmsg = await sendMessage(nextmsg, " ".join(msg))
        nextmsg = await client.get_messages(chat_id=message.chat.id, message_ids=nextmsg.id)
        if folder_name:
            sameDir['tasks'].add(nextmsg.id)
        nextmsg.from_user = message.from_user
        await sleep(5)
        _mirror_leech(client, nextmsg, isQbit, isLeech, sameDir, bulk)

    __run_multi()

    path = f'{DOWNLOAD_DIR}{message.id}{folder_name}'

    if len(text) > 1 and text[1].startswith('Tag: '):
        tag, id_ = text[1].split('Tag: ')[1].split()
        message.from_user = await client.get_users(id_)
        try:
            await message.unpin()
        except:
            pass
    elif sender_chat := message.sender_chat:
        tag = sender_chat.title
    if username := message.from_user.username:
        tag = f"@{username}"
    else:
        tag = message.from_user.mention

    if link and is_telegram_link(link):
        try:
            reply_to, session = await get_tg_link_content(link)
        except Exception as e:
            await sendMessage(message, f'ERROR: {e}')
            await delete_links(message)
            return
    elif not link and (reply_to := message.reply_to_message):
        if reply_to.text:
            reply_text = reply_to.text.split('\n', 1)[0].strip()
            if reply_text and is_telegram_link(reply_text):
                try:
                    reply_to, session = await get_tg_link_content(reply_text)
                except Exception as e:
                    await sendMessage(message, f'ERROR: {e}')
                    await delete_links(message)
                    return

    if reply_to:
        file_ = getattr(reply_to, reply_to.media.value) if reply_to.media else None
        if file_ is None:
            reply_text = reply_to.text.split('\n', 1)[0].strip()
            if is_url(reply_text) or is_magnet(reply_text):
                link = reply_text
        elif reply_to.document and (file_.mime_type == 'application/x-bittorrent' or file_.file_name.endswith('.torrent')):
            link = await reply_to.download()
            file_ = None

    if not is_url(link) and not is_magnet(link) and not await aiopath.exists(link) and not is_rclone_path(link) and file_ is None:
        reply_message = await sendMessage(message, MIRROR_HELP_MESSAGE)
        await auto_delete_message(message, reply_message)
        await delete_links(message)
        return

    error_msg = []
    error_button = None
    task_utilis_msg, error_button = await task_utils(message)
    if task_utilis_msg:
        error_msg.extend(task_utilis_msg)

    if error_msg:
        final_msg = f'Hey, <b>{tag}</b>,\n'
        for __i, __msg in enumerate(error_msg, 1):
            final_msg += f'\n<b>{__i}</b>: {__msg}\n'
        if error_button is not None:
            error_button = error_button.build_menu(2)
        await sendMessage(message, final_msg, error_button)
        await delete_links(message)
        return

    if link:
        LOGGER.info(link)

    if not is_mega_link(link) and not isQbit and not is_magnet(link) and not is_rclone_path(link) \
       and not is_gdrive_link(link) and not link.endswith('.torrent') and file_ is None:
        content_type = await get_content_type(link)
        if content_type is None or re_match(r'text/html|text/plain', content_type):
            process_msg = await sendMessage(message, f"<i><b>Processing:</b></i> <code>{link}</code>")
            try:
                link = await sync_to_async(direct_link_generator, link)
                LOGGER.info(f"Generated link: {link}")
                await editMessage(process_msg, f"<i><b>Generated link:</b></i> <code>{link}</code>")
            except DirectDownloadLinkException as e:
                LOGGER.info(str(e))
                if str(e).startswith('ERROR:'):
                    await editMessage(process_msg, str(e))
                    await delete_links(message)
                    return
            await process_msg.delete()

    if not isLeech:
        if config_dict['DEFAULT_UPLOAD'] == 'rc' and not up or up == 'rc':
            up = config_dict['RCLONE_PATH']
        elif config_dict['DEFAULT_UPLOAD'] == 'ddl' and not up or up == 'ddl':
            up = 'ddl'
        if not up and config_dict['DEFAULT_UPLOAD'] == 'gd':
            up = 'gd'
        if up == 'gd' and not config_dict['GDRIVE_ID']:
            await sendMessage(message, 'GDRIVE_ID not Provided!')
            return
        elif not up:
            await sendMessage(message, 'No RClone Destination!')
            await delete_links(message)
            return
        elif up not in ['rcl', 'gd', 'ddl']:
            if up.startswith('mrcc:'):
                config_path = f'rclone/{message.from_user.id}.conf'
            else:
                config_path = 'rclone.conf'
            if not await aiopath.exists(config_path):
                await sendMessage(message, f"RClone Config: {config_path} not Exists!")
                await delete_links(message)
                return
        if up != 'gd' and up != 'ddl' and not is_rclone_path(up):
            await sendMessage(message, 'Wrong Rclone Upload Destination!')
            await delete_links(message)
            return

    if link == 'rcl':
        link = await RcloneList(client, message).get_rclone_path('rcd')
        if not is_rclone_path(link):
            await sendMessage(message, link)
            await delete_links(message)
            return

    if up == 'rcl' and not isLeech:
        up = await RcloneList(client, message).get_rclone_path('rcu')
        if not is_rclone_path(up):
            await sendMessage(message, up)
            await delete_links(message)
            return

    listener = MirrorLeechListener(message, compress, extract, isQbit, isLeech, tag, select, seed, sameDir, rcf, up, join, source_url=link)

    if file_ is not None:
        await TelegramDownloadHelper(listener).add_download(reply_to, f'{path}/', name, session)
    elif is_rclone_path(link):
        if link.startswith('mrcc:'):
            link = link.split('mrcc:', 1)[1]
            config_path = f'rclone/{message.from_user.id}.conf'
        else:
            config_path = 'rclone.conf'
        if not await aiopath.exists(config_path):
            await sendMessage(message, f"<b>RClone Config:</b> {config_path} not Exists!")
            await delete_links(message)
            return
        await add_rclone_download(link, config_path, f'{path}/', name, listener)
    elif is_gdrive_link(link):
        await delete_links(message)
        await add_gd_download(link, path, listener, name)
    elif is_mega_link(link):
        await delete_links(message)
        await add_mega_download(link, f'{path}/', listener, name)
    elif isQbit:
        await add_qb_torrent(link, path, listener, ratio, seed_time)
    else:
        ussr = args['-au']
        pssw = args['-ap']
        if ussr or pssw:
            auth = f"{ussr}:{pssw}"
            auth = "Basic " + b64encode(auth.encode()).decode('ascii')
        else:
            auth = ''
        await add_aria2c_download(link, path, listener, name, auth, ratio, seed_time)
    await delete_links(message)



async def mirror(client, message):
    _mirror_leech(client, message)


async def qb_mirror(client, message):
    _mirror_leech(client, message, isQbit=True)


async def leech(client, message):
    _mirror_leech(client, message, isLeech=True)


async def qb_leech(client, message):
    _mirror_leech(client, message, isQbit=True, isLeech=True)


bot.add_handler(MessageHandler(mirror, filters=command(
    BotCommands.MirrorCommand) & CustomFilters.authorized))
bot.add_handler(MessageHandler(qb_mirror, filters=command(
    BotCommands.QbMirrorCommand) & CustomFilters.authorized))
bot.add_handler(MessageHandler(leech, filters=command(
    BotCommands.LeechCommand) & CustomFilters.authorized))
bot.add_handler(MessageHandler(qb_leech, filters=command(
    BotCommands.QbLeechCommand) & CustomFilters.authorized))