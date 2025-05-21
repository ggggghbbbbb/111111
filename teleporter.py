#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple, Union

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, ChannelPrivateError
from telethon.tl.types import Channel, Chat, Message, PeerChannel, PeerChat, PeerUser, User, MessageMediaDocument, MessageMediaPhoto
from telethon.tl.functions.messages import GetDiscussionMessageRequest

# 配置日志记录
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)
logger = logging.getLogger(__name__)

# API 配置
API_ID = 27234765
API_HASH = "db5cdb354c4a69e82c17f70c253a63e3"
PHONE = "+2349026478413"
PASSWORD = "20010201ghbBHG"

# 白名单用户列表，只有这些用户可以操作机器人
ALLOWED_USERS = {6360839781, 7696971263, 5904666183}

# 数据文件路径
RULES_FILE = "rules.json"
USER_STATE_FILE = "user_state.json"

# 用户状态数据结构
user_states = {}

# 转发规则数据结构
forwarding_rules = {}

# 最后查看的消息ID记录
last_message_ids = {}

class BotState:
    IDLE = 0
    WAITING_SOURCE = 1
    WAITING_TARGET = 2

class UserState:
    def __init__(self):
        self.state = BotState.IDLE
        self.temp_rule_name = ""
        self.source_chat_id = None
        self.target_chat_id = None

def save_rules():
    """保存转发规则到文件"""
    with open(RULES_FILE, 'w', encoding='utf-8') as f:
        json.dump(forwarding_rules, f, ensure_ascii=False, indent=2)
    logger.info(f"规则已保存到 {RULES_FILE}")

def load_rules():
    """从文件加载转发规则"""
    global forwarding_rules, last_message_ids
    if os.path.exists(RULES_FILE):
        try:
            with open(RULES_FILE, 'r', encoding='utf-8') as f:
                forwarding_rules = json.load(f)
            
            # 初始化最后查看的消息ID
            for rule_name, rule in forwarding_rules.items():
                source_chat_id = rule["source_chat_id"]
                if source_chat_id not in last_message_ids:
                    last_message_ids[source_chat_id] = 0
            
            logger.info(f"已从 {RULES_FILE} 加载 {len(forwarding_rules)} 条规则")
        except Exception as e:
            logger.error(f"加载规则失败: {e}")
            forwarding_rules = {}
    else:
        logger.info("规则文件不存在，创建新的规则集")
        forwarding_rules = {}

def save_user_states():
    """保存用户状态到文件"""
    states_to_save = {}
    for user_id, state in user_states.items():
        states_to_save[str(user_id)] = {
            "state": state.state,
            "temp_rule_name": state.temp_rule_name,
            "source_chat_id": state.source_chat_id,
            "target_chat_id": state.target_chat_id
        }
    
    with open(USER_STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(states_to_save, f, ensure_ascii=False, indent=2)
    logger.info(f"用户状态已保存到 {USER_STATE_FILE}")

def load_user_states():
    """从文件加载用户状态"""
    global user_states
    if os.path.exists(USER_STATE_FILE):
        try:
            with open(USER_STATE_FILE, 'r', encoding='utf-8') as f:
                states_data = json.load(f)
            
            for user_id, state_data in states_data.items():
                user_id = int(user_id)
                state = UserState()
                state.state = state_data["state"]
                state.temp_rule_name = state_data["temp_rule_name"]
                state.source_chat_id = state_data["source_chat_id"]
                state.target_chat_id = state_data["target_chat_id"]
                user_states[user_id] = state
            
            logger.info(f"已从 {USER_STATE_FILE} 加载 {len(user_states)} 个用户状态")
        except Exception as e:
            logger.error(f"加载用户状态失败: {e}")
            user_states = {}
    else:
        logger.info("用户状态文件不存在，创建新的用户状态集")
        user_states = {}

async def is_user_allowed(user_id: int) -> bool:
    """检查用户是否在白名单中"""
    return user_id in ALLOWED_USERS

async def get_chat_id_from_message(message: Message) -> Optional[int]:
    """从消息中提取聊天/频道ID"""
    if message.forward:
        if hasattr(message.forward.from_id, 'channel_id'):
            return -1000000000000 - message.forward.from_id.channel_id
        elif message.forward.chat_id:
            if message.forward.chat_id < 0:
                return message.forward.chat_id
    return None

async def add_new_rule(user_id: int, rule_name: str):
    """添加新的转发规则流程"""
    if rule_name in forwarding_rules:
        return f"规则名 '{rule_name}' 已存在，请使用其他名称。"
    
    if user_id not in user_states:
        user_states[user_id] = UserState()
    
    user_states[user_id].state = BotState.WAITING_SOURCE
    user_states[user_id].temp_rule_name = rule_name
    save_user_states()
    
    return "请转发一条源群组/频道的消息，用于获取源群组/频道的ID。"

async def process_source_message(user_id: int, message: Message):
    """处理用户发送的源群组/频道消息"""
    chat_id = await get_chat_id_from_message(message)
    
    if not chat_id:
        return "解析失败，无法获取源群组/频道ID，请重新转发一条源群组/频道的消息。"
    
    user_states[user_id].source_chat_id = chat_id
    user_states[user_id].state = BotState.WAITING_TARGET
    save_user_states()
    
    return "请转发一条目标群组/频道的消息，用于获取目标群组/频道的ID。"

async def process_target_message(user_id: int, message: Message):
    """处理用户发送的目标群组/频道消息"""
    chat_id = await get_chat_id_from_message(message)
    
    if not chat_id:
        return "解析失败，无法获取目标群组/频道ID，请重新转发一条目标群组/频道的消息。"
    
    rule_name = user_states[user_id].temp_rule_name
    source_chat_id = user_states[user_id].source_chat_id
    
    # 添加新规则
    forwarding_rules[rule_name] = {
        "source_chat_id": source_chat_id,
        "target_chat_id": chat_id,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "created_by": user_id
    }
    
    # 初始化最后查看的消息ID
    if source_chat_id not in last_message_ids:
        last_message_ids[source_chat_id] = 0
    
    # 重置用户状态
    user_states[user_id].state = BotState.IDLE
    user_states[user_id].temp_rule_name = ""
    user_states[user_id].source_chat_id = None
    user_states[user_id].target_chat_id = None
    
    save_rules()
    save_user_states()
    
    return f"规则【{rule_name}】添加成功，开始转发。"

async def list_rules():
    """列出所有转发规则"""
    if not forwarding_rules:
        return "当前没有转发规则。"
    
    result = "当前转发规则列表：\n\n"
    for name, rule in forwarding_rules.items():
        result += f"规则名：{name}\n"
        result += f"源群组/频道ID：{rule['source_chat_id']}\n"
        result += f"目标群组/频道ID：{rule['target_chat_id']}\n"
        result += f"创建时间：{rule['created_at']}\n"
        result += f"创建者ID：{rule['created_by']}\n"
        result += "-" * 30 + "\n"
    
    return result

async def delete_rule(rule_name: str):
    """删除转发规则"""
    if rule_name not in forwarding_rules:
        return f"规则 '{rule_name}' 不存在。"
    
    del forwarding_rules[rule_name]
    save_rules()
    return f"规则 '{rule_name}' 已删除。"

async def forward_messages(client: TelegramClient, source_chat_id: int, target_chat_id: int):
    """转发消息从源群组/频道到目标群组/频道"""
    try:
        # 获取源群组的历史消息
        last_id = last_message_ids.get(source_chat_id, 0)
        
        # 初始化从最早到最晚的消息转发
        if last_id == 0:
            # 从头开始克隆
            # 我们先获取总消息数量的估计
            total_count = 0
            async for _ in client.iter_messages(source_chat_id, limit=1):
                total_count += 1
            
            logger.info(f"开始初始克隆，源群组/频道总消息数约为: {total_count}")
            
            # 如果是初次克隆，我们从最早的消息开始
            # 获取最早的最多200条消息
            earliest_messages = []
            async for message in client.iter_messages(source_chat_id, limit=200, reverse=True):
                earliest_messages.append(message)
            
            if earliest_messages:
                logger.info(f"获取到 {len(earliest_messages)} 条历史消息")
                
                # 按照ID排序确保顺序
                earliest_messages.sort(key=lambda m: m.id)
                
                # 按群组，将相同albumID的消息分组
                album_groups = {}
                standalone_messages = []
                
                for message in earliest_messages:
                    # 如果消息是相册的一部分
                    if hasattr(message, 'grouped_id') and message.grouped_id:
                        if message.grouped_id not in album_groups:
                            album_groups[message.grouped_id] = []
                        album_groups[message.grouped_id].append(message)
                    else:
                        standalone_messages.append(message)
                
                # 转发所有相册（多媒体组）
                forwarded_count = 0
                for album_id, album_messages in album_groups.items():
                    try:
                        # 排序相册消息
                        album_messages.sort(key=lambda m: m.id)
                        
                        # 从相册中提取所有媒体文件
                        media_files = []
                        caption = None
                        
                        for msg in album_messages:
                            if msg.media:
                                media_files.append(msg.media)
                                # 使用第一个有文本的消息作为标题
                                if not caption and msg.message:
                                    caption = msg.message
                        
                        # 一次性发送整个相册
                        if media_files:
                            await client.send_file(
                                target_chat_id,
                                file=media_files,  # 发送多个媒体文件
                                caption=caption if caption else "",
                                parse_mode='md',
                                silent=album_messages[0].silent if album_messages else False
                            )
                            forwarded_count += 1
                            
                            # 更新最后处理的消息ID
                            last_message_id = max(m.id for m in album_messages)
                            last_message_ids[source_chat_id] = max(last_message_ids.get(source_chat_id, 0), last_message_id)
                            
                            # 休息一秒防止封号
                            await asyncio.sleep(1)
                    except Exception as e:
                        logger.error(f"转发相册失败 (AlbumID: {album_id}): {e}")
                
                # 转发独立消息
                for message in standalone_messages:
                    # 不转发系统消息、表情包和投票
                    if message.action or not message.message and not message.media:
                        continue
                    
                    # 转发消息（无引用）
                    try:
                        await client.send_message(
                            target_chat_id,
                            message.message,
                            file=message.media,
                            silent=message.silent,
                            parse_mode='md'
                        )
                        forwarded_count += 1
                        
                        # 更新最后处理的消息ID
                        last_message_ids[source_chat_id] = max(last_message_ids.get(source_chat_id, 0), message.id)
                        
                        # 休息一秒，防止封号
                        await asyncio.sleep(1)
                    except Exception as e:
                        logger.error(f"转发消息失败 (ID: {message.id}): {e}")
                
                return forwarded_count
        else:
            # 获取新消息
            messages = await client.get_messages(
                source_chat_id, 
                limit=50,  # 一次获取更多消息
                min_id=last_id
            )
            
            if not messages:
                return 0
            
            # 按照ID排序，确保按发送顺序转发
            messages = sorted(messages, key=lambda m: m.id)
            
            # 按群组，将相同albumID的消息分组
            album_groups = {}
            standalone_messages = []
            
            for message in messages:
                # 如果消息是相册的一部分
                if hasattr(message, 'grouped_id') and message.grouped_id:
                    if message.grouped_id not in album_groups:
                        album_groups[message.grouped_id] = []
                    album_groups[message.grouped_id].append(message)
                else:
                    standalone_messages.append(message)
            
            # 转发所有相册（多媒体组）
            forwarded_count = 0
            for album_id, album_messages in album_groups.items():
                try:
                    # 排序相册消息
                    album_messages.sort(key=lambda m: m.id)
                    
                    # 从相册中提取所有媒体文件
                    media_files = []
                    caption = None
                    
                    for msg in album_messages:
                        if msg.media:
                            media_files.append(msg.media)
                            # 使用第一个有文本的消息作为标题
                            if not caption and msg.message:
                                caption = msg.message
                    
                    # 一次性发送整个相册
                    if media_files:
                        await client.send_file(
                            target_chat_id,
                            file=media_files,  # 发送多个媒体文件
                            caption=caption if caption else "",
                            parse_mode='md',
                            silent=album_messages[0].silent if album_messages else False
                        )
                        forwarded_count += 1
                        
                        # 更新最后处理的消息ID
                        last_message_id = max(m.id for m in album_messages)
                        last_message_ids[source_chat_id] = max(last_message_ids[source_chat_id], last_message_id)
                        
                        # 休息一秒防止封号
                        await asyncio.sleep(1)
                except Exception as e:
                    logger.error(f"转发相册失败 (AlbumID: {album_id}): {e}")
            
            # 转发独立消息
            for message in standalone_messages:
                # 不转发系统消息、表情包和投票
                if message.action or not message.message and not message.media:
                    continue
                
                # 转发消息（无引用）
                try:
                    await client.send_message(
                        target_chat_id,
                        message.message,
                        file=message.media,
                        silent=message.silent,
                        parse_mode='md'
                    )
                    forwarded_count += 1
                    
                    # 更新最后处理的消息ID
                    last_message_ids[source_chat_id] = max(last_message_ids[source_chat_id], message.id)
                    
                    # 休息一秒，防止封号
                    await asyncio.sleep(1)
                except Exception as e:
                    logger.error(f"转发消息失败 (ID: {message.id}): {e}")
            
            return forwarded_count
    except Exception as e:
        logger.error(f"获取或转发消息失败: {e}")
        if isinstance(e, FloodWaitError):
            logger.info(f"需要等待 {e.seconds} 秒")
            await asyncio.sleep(e.seconds)
        return 0

async def forward_comment_messages(client: TelegramClient, source_chat_id: int, target_chat_id: int):
    """转发频道评论区消息"""
    try:
        # 尝试获取频道的评论区
        # 由于GetRepliesRequest直接使用有困难，我们改用另一种方法
        try:
            # 首先获取一条源频道消息
            source_messages = await client.get_messages(source_chat_id, limit=1)
            if not source_messages:
                return 0
                
            # 获取频道实体
            source_entity = await client.get_entity(source_chat_id)
            
            # 如果是频道，尝试找到其讨论组
            if isinstance(source_entity, Channel) and source_entity.megagroup == False:
                # 检查频道是否有链接的讨论组
                if hasattr(source_entity, 'linked_chat_id') and source_entity.linked_chat_id:
                    discussion_chat_id = source_entity.linked_chat_id
                    
                    # 转发评论消息
                    last_id = last_message_ids.get(discussion_chat_id, 0)
                    messages = await client.get_messages(
                        discussion_chat_id,
                        limit=10,
                        min_id=last_id
                    )
                    
                    if not messages:
                        return 0
                        
                    messages = sorted(messages, key=lambda m: m.id)
                    
                    forwarded_count = 0
                    for message in messages:
                        # 跳过系统消息和空消息
                        if message.action or not message.message and not message.media:
                            continue
                            
                        # 转发消息
                        try:
                            await client.send_message(
                                target_chat_id,
                                f"💬 评论: {message.message}",
                                file=message.media,
                                silent=message.silent
                            )
                            forwarded_count += 1
                            
                            # 更新最后处理的消息ID
                            if discussion_chat_id not in last_message_ids:
                                last_message_ids[discussion_chat_id] = 0
                            last_message_ids[discussion_chat_id] = max(last_message_ids[discussion_chat_id], message.id)
                            
                            # 休息一秒，防止封号
                            await asyncio.sleep(1)
                        except Exception as e:
                            logger.error(f"转发评论消息失败 (ID: {message.id}): {e}")
                    
                    return forwarded_count
        except Exception as e:
            logger.error(f"获取评论区失败: {e}")
        
        return 0
    except Exception as e:
        logger.error(f"转发评论失败: {e}")
        return 0

async def check_new_messages(client: TelegramClient):
    """检查所有规则的新消息"""
    while True:
        try:
            total_forwarded = 0
            for rule_name, rule in forwarding_rules.items():
                source_id = rule["source_chat_id"]
                target_id = rule["target_chat_id"]
                
                # 转发主频道/群组消息
                forwarded = await forward_messages(client, source_id, target_id)
                total_forwarded += forwarded
                
                # 尝试转发评论区消息
                comment_forwarded = await forward_comment_messages(client, source_id, target_id)
                total_forwarded += comment_forwarded
                
                if forwarded > 0 or comment_forwarded > 0:
                    logger.info(f"规则 '{rule_name}' 转发了 {forwarded + comment_forwarded} 条消息")
            
            if total_forwarded > 0:
                logger.info(f"本轮共转发了 {total_forwarded} 条消息")
            
            # 等待20秒后再次检查
            await asyncio.sleep(20)
        except Exception as e:
            logger.error(f"检查新消息过程中出错: {e}")
            await asyncio.sleep(30)  # 出错后等待长一点的时间

async def handle_user_message(event):
    """处理用户消息"""
    # 获取发送者ID
    sender = await event.get_sender()
    user_id = sender.id
    
    # 检查用户是否在白名单中
    if not await is_user_allowed(user_id):
        return
    
    message = event.message
    text = message.text if message.text else ""
    
    # 处理命令
    if text.startswith("/"):
        parts = text.split()
        command = parts[0].lower()
        
        if command == "/add" and len(parts) > 1:
            rule_name = " ".join(parts[1:])
            response = await add_new_rule(user_id, rule_name)
            await event.respond(response)
        elif command == "/list":
            response = await list_rules()
            await event.respond(response)
        elif command == "/delete" and len(parts) > 1:
            rule_name = " ".join(parts[1:])
            response = await delete_rule(rule_name)
            await event.respond(response)
        elif command == "/help":
            help_text = (
                "机器人命令列表：\n"
                "/add [规则名] - 添加新的转发规则\n"
                "/list - 列出所有转发规则\n"
                "/delete [规则名] - 删除指定的转发规则\n"
                "/help - 显示此帮助信息"
            )
            await event.respond(help_text)
        return
    
    # 处理用户状态
    if user_id in user_states:
        state = user_states[user_id].state
        
        if state == BotState.WAITING_SOURCE:
            response = await process_source_message(user_id, message)
            await event.respond(response)
        elif state == BotState.WAITING_TARGET:
            response = await process_target_message(user_id, message)
            await event.respond(response)

async def main():
    # 加载规则和用户状态
    load_rules()
    load_user_states()
    
    # 创建客户端
    client = TelegramClient('session_name', API_ID, API_HASH)
    
    # 注册消息处理器
    @client.on(events.NewMessage)
    async def on_message(event):
        await handle_user_message(event)
    
    # 启动客户端
    await client.start()
    logger.info("机器人已启动")
    
    # 如果需要，进行登录验证
    if not await client.is_user_authorized():
        await client.sign_in(PHONE, code=None, password=PASSWORD)
        logger.info("已登录")
    
    # 启动消息检查任务
    asyncio.create_task(check_new_messages(client))
    
    # 保持客户端运行
    await client.run_until_disconnected()

if __name__ == "__main__":
    # 运行主函数
    asyncio.run(main())