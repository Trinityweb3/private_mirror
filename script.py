import asyncio
import os
import sys
import logging
import re
from datetime import datetime
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.tl.types import MessageService, MessageEntityMention
from telethon.tl import functions, types
from telethon.errors import FloodWaitError

load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_NAME = os.getenv("SESSION_NAME", "mirror_session")

SOURCE_CHAT = int(os.getenv("SOURCE_CHAT"))
TARGET_CHAT = int(os.getenv("TARGET_CHAT"))

TOPIC_MAP = {
   7:2,
   10:4,
   9:7,
   8:6,
   5:8,
   6:9,
   14:10,
   11:11,
   19:12,
   16:13,
   15:14,
   7:15,
   17:16,
   18:17,
   21:18,
   12:19,
   20:20
}


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('mirror_bot.log')
    ]
)
logger = logging.getLogger(__name__)

client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

class MirrorBot:
    def __init__(self):
        self.last_processed_ids = {}
        self.source_entity = None
        self.target_entity = None
        self.is_processing = False
        self.initialized = False
        self.flood_wait_count = 0
        
    def get_topic_id(self, msg):
        """Правильно извлекает topic_id из сообщения"""
        if not msg.reply_to:
            return None
            
        reply = msg.reply_to
        # Проверяем различные варианты хранения topic_id
        if hasattr(reply, 'reply_to_top_id') and reply.reply_to_top_id:
            return reply.reply_to_top_id
        elif hasattr(reply, 'reply_to_msg_id') and reply.reply_to_msg_id:
            return reply.reply_to_msg_id
        elif hasattr(reply, 'forum_topic') and reply.forum_topic:
            if hasattr(reply, 'reply_to_top_id'):
                return reply.reply_to_top_id
            elif hasattr(reply, 'reply_to_msg_id'):
                return reply.reply_to_msg_id
        return None
    
    def remove_mentions_from_text(self, text, entities):
        """Удаляет упоминания (@username) из текста"""
        if not text or not entities:
            return text
            
        # Сортируем entities по offset в обратном порядке
        # чтобы удалять с конца и не сбивать offsets
        mentions = []
        for entity in entities:
            if isinstance(entity, MessageEntityMention):
                mentions.append(entity)
        
        # Сортируем по offset в обратном порядке
        mentions.sort(key=lambda x: x.offset, reverse=True)
        
        result_text = text
        for mention in mentions:
            start = mention.offset
            end = mention.offset + mention.length
            # Удаляем упоминание из текста
            result_text = result_text[:start] + result_text[end:]            
        return result_text
    
    async def initialize_last_ids(self):
        """Инициализирует последние ID сообщений для каждого топика"""
        logger.info("🔍 Инициализация последних ID сообщений...")
        
        for src_topic_id in TOPIC_MAP.keys():
            try:
                messages = await client.get_messages(
                    self.source_entity,
                    limit=3,
                    reply_to=src_topic_id
                )
                
                last_msg_id = 0
                for msg in messages:
                    if not isinstance(msg, MessageService):
                        last_msg_id = max(last_msg_id, msg.id)
                
                self.last_processed_ids[src_topic_id] = last_msg_id
                logger.info(f"   Топик {src_topic_id}: последний ID = {last_msg_id}")
                    
            except Exception as e:
                logger.error(f"Ошибка при инициализации топика {src_topic_id}: {e}")
                self.last_processed_ids[src_topic_id] = 0
        
        self.initialized = True
        logger.info("✅ Инициализация завершена")
    
    async def forward_message(self, msg, src_topic_id):
        """Пересылает одно сообщение в целевой топик с удалением упоминаний"""
        try:
            if isinstance(msg, MessageService):
                return False
                
            dst_topic_id = TOPIC_MAP.get(src_topic_id)
            if not dst_topic_id:
                logger.warning(f"Не найден целевой топик для исходного топика {src_topic_id}")
                return False
            
            # Получаем текст и сущности сообщения
            text = msg.message
            entities = msg.entities
            
            # Удаляем упоминания из текста
            cleaned_text = self.remove_mentions_from_text(text, entities)
            
            # Проверяем, является ли медиа веб-страницей
            from telethon.tl.types import MessageMediaWebPage
            
            # Если есть медиа И это не веб-страница
            if msg.media and not isinstance(msg.media, MessageMediaWebPage):
                # Если после очистки текст не пустой
                if cleaned_text and cleaned_text.strip():
                    # Отправляем с медиа и очищенным текстом
                    await client.send_file(
                        self.target_entity,
                        file=msg.media,
                        caption=cleaned_text,
                        reply_to=dst_topic_id
                    )
                else:
                    # Если нет текста, отправляем только медиа
                    await client.send_file(
                        self.target_entity,
                        file=msg.media,
                        reply_to=dst_topic_id
                    )
                
                self.last_processed_ids[src_topic_id] = msg.id
                logger.info(f"📤 Переслано (с медиа): топик {src_topic_id} → {dst_topic_id}, ID={msg.id}")
                return True
            
            # Если это веб-страница ИЛИ сообщение без медиа
            else:
                # Для веб-страницы отправляем только текст (ссылку)
                # Telegram сам развернет превью в целевом чате
                
                # Если текст пустой, но есть веб-страница, пропускаем
                # (веб-превью без текста не имеет смысла пересылать отдельно)
                if cleaned_text and cleaned_text.strip():
                    await client.send_message(
                        self.target_entity,
                        cleaned_text,
                        reply_to=dst_topic_id
                    )
                    
                    self.last_processed_ids[src_topic_id] = msg.id
                    logger.info(f"📤 Переслано (текст/ссылка): топик {src_topic_id} → {dst_topic_id}, ID={msg.id}")
                    return True
                elif msg.media and isinstance(msg.media, MessageMediaWebPage):
                    # Если это веб-превью без текста, можно пропустить
                    logger.info(f"⏭️ Пропущено веб-превью без текста: топик {src_topic_id}, ID={msg.id}")
                    return False
                else:
                    # Пустое сообщение без текста и медиа
                    logger.info(f"⏭️ Пропущено пустое сообщение: топик {src_topic_id}, ID={msg.id}")
                    return False
                
        except FloodWaitError as e:
            wait_time = e.seconds
            logger.warning(f"⏳ Flood wait {wait_time} секунд")
            await asyncio.sleep(wait_time)
            return await self.forward_message(msg, src_topic_id)
        except Exception as e:
            error_msg = str(e)
            if "TOPIC_CLOSED" in error_msg:
                logger.warning(f"Топик {dst_topic_id} закрыт, нельзя отправлять сообщения")
            elif "TOPIC_NOT_MODIFIED" in error_msg:
                logger.warning(f"Топик {dst_topic_id} не модифицирован, возможно отсутствуют права")
            elif "PEER_ID_INVALID" in error_msg:
                logger.error(f"Некорректный ID чата/топика: {error_msg}")
            elif "MESSAGE_ID_INVALID" in error_msg:
                logger.warning(f"Некорректный ID сообщения: {msg.id}")
            else:
                logger.error(f"Ошибка при пересылке сообщения {msg.id}: {error_msg}")
            return False
    
    async def check_new_messages(self):
        """Периодически проверяет новые сообщения во всех топиках"""
        logger.info("🔄 Запуск периодической проверки новых сообщений...")
        
        while True:
            try:
                for src_topic_id in TOPIC_MAP.keys():
                    try:
                        last_id = self.last_processed_ids.get(src_topic_id, 0)
                        
                        messages = await client.get_messages(
                            self.source_entity,
                            limit=10,
                            reply_to=src_topic_id
                        )
                        
                        new_messages = []
                        for msg in messages:
                            if not isinstance(msg, MessageService) and msg.id > last_id:
                                new_messages.append(msg)
                        
                        new_messages.sort(key=lambda x: x.id)
                        
                        for msg in new_messages:
                            await self.forward_message(msg, src_topic_id)
                            await asyncio.sleep(1)  
                            
                    except Exception as e:
                        logger.error(f"Ошибка при проверке топика {src_topic_id}: {e}")
                        await asyncio.sleep(5)
                
                await asyncio.sleep(30)  
                
            except Exception as e:
                logger.error(f"Ошибка в основном цикле проверки: {e}")
                await asyncio.sleep(60)
    
    async def handle_new_message(self, event):
        """Обработчик новых сообщений в реальном времени"""
        try:
            msg = event.message
            src_topic_id = self.get_topic_id(msg)
            
            if not src_topic_id or src_topic_id not in TOPIC_MAP:
                if src_topic_id:
                    logger.debug(f"Сообщение из топика {src_topic_id} не входит в TOPIC_MAP")
                return
            
            logger.info(f"📨 Новое сообщение в топике {src_topic_id}, ID={msg.id}")
            
            if not isinstance(msg, MessageService):
                await self.forward_message(msg, src_topic_id)
                
        except Exception as e:
            logger.error(f"Ошибка обработки нового сообщения: {e}")
    
    async def run(self):
        """Основной метод запуска демона"""
        logger.info("🚀 Запуск Mirror Bot...")
        
        try:
            await client.start()
            logger.info("✅ Успешно подключились к Telegram")
            
            self.source_entity = await client.get_entity(SOURCE_CHAT)
            self.target_entity = await client.get_entity(TARGET_CHAT)
            
            logger.info(f"📁 Исходный чат: {self.source_entity.title}")
            logger.info(f"📁 Целевой чат: {self.target_entity.title}")
            
            await self.initialize_last_ids()
            
            @client.on(events.NewMessage(chats=SOURCE_CHAT))
            async def handler(event):
                await self.handle_new_message(event)
            
            logger.info("👂 Начинаем слушать новые сообщения в реальном времени...")
            logger.info("🔄 Периодическая проверка запущена...")
            
            asyncio.create_task(self.check_new_messages())
            
            await client.run_until_disconnected()
            
        except KeyboardInterrupt:
            logger.info("⏹ Остановка по запросу пользователя")
        except Exception as e:
            logger.error(f"Критическая ошибка: {e}")
        finally:
            logger.info("👋 Завершение работы")

async def main():
    bot = MirrorBot()
    await bot.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nДемон остановлен")
    except Exception as e:
        print(f"Ошибка запуска: {e}")