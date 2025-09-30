import os
import logging
import json
import smtplib
import re
import urllib.parse
import traceback
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Dict, Any, List
from datetime import datetime, timedelta
from pathlib import Path
import ics

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.utils.markdown import hbold, hitalic

import openai
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Инициализация бота и диспетчера
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_IDS = [int(id.strip()) for id in os.getenv("ADMIN_IDS", "").split(",") if id.strip()]

if not BOT_TOKEN:
    exit("Ошибка: Не задан BOT_TOKEN в .env файле")

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

if OPENAI_API_KEY and OPENAI_API_KEY != "ВАШ_OPENAI_API_KEY":
    openai.api_key = OPENAI_API_KEY
else:
    logger.warning("OpenAI API ключ не задан или имеет значение по умолчанию. AI функции будут ограничены.")


# Хранилище данных кандидатов (JSON файл)
def load_candidates_data():
    """Загружает данные кандидатов из JSON файла"""
    file_path = Path("candidates_data.json")
    if file_path.exists():
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Ошибка загрузки данных из файла: {e}")
            return {}
    return {}


def save_candidates_data(data):
    """Сохраняет данные кандидатов в JSON файл"""
    try:
        with open("candidates_data.json", 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Ошибка сохранения данных в файл: {e}")


# Хранение данных рекрутеров
def load_recruiters_data():
    """Загружает данные рекрутеров из JSON файла"""
    file_path = Path("recruiters_data.json")
    if file_path.exists():
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Ошибка загрузки данных рекрутеров из файла: {e}")
            return {}
    return {}


def save_recruiters_data(data):
    """Сохраняет данные рекрутеров в JSON файл"""
    try:
        with open("recruiters_data.json", 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Ошибка сохранения данных рекрутеров в файл: {e}")


# Хранение данных пользователей с доступом
def load_users_data():
    """Загружает данные пользователей с доступом из JSON файла"""
    file_path = Path("users_data.json")
    if file_path.exists():
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Ошибка загрузки данных пользователей из файла: {e}")
            return {}
    return {}


def save_users_data(data):
    """Сохраняет данные пользователей с доступом в JSON файл"""
    try:
        with open("users_data.json", 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Ошибка сохранения данных пользователей в файл: {e}")


# Загружаем данные при старте
candidates_data = load_candidates_data()
recruiters_data = load_recruiters_data()
users_data = load_users_data()


# Функции для управления доступом
def check_user_access(user_id: int) -> bool:
    """Проверяет есть ли у пользователя доступ к боту"""
    user_id_str = str(user_id)

    # Админы всегда имеют доступ
    if user_id in ADMIN_IDS:
        return True

    # Проверяем наличие пользователя в базе
    if user_id_str not in users_data:
        return False

    user_data = users_data[user_id_str]

    # Проверяем срок действия доступа
    if 'access_until' in user_data:
        try:
            access_until = datetime.fromisoformat(user_data['access_until'])
            if datetime.now() < access_until:
                return True
            else:
                # Срок истек, удаляем пользователя
                del users_data[user_id_str]
                save_users_data(users_data)
                return False
        except ValueError:
            return False

    return False


def grant_access(user_id: int, duration_days: int = 30) -> bool:
    """Выдает доступ пользователю на указанное количество дней"""
    try:
        access_until = datetime.now() + timedelta(days=duration_days)
        users_data[str(user_id)] = {
            'access_granted': datetime.now().isoformat(),
            'access_until': access_until.isoformat(),
            'granted_by': 'admin'
        }
        save_users_data(users_data)
        return True
    except Exception as e:
        logger.error(f"Ошибка при выдаче доступа: {e}")
        return False


def revoke_access(user_id: int) -> bool:
    """Отзывает доступ у пользователя"""
    try:
        user_id_str = str(user_id)
        if user_id_str in users_data:
            del users_data[user_id_str]
            save_users_data(users_data)
        return True
    except Exception as e:
        logger.error(f"Ошибка при отзыве доступа: {e}")
        return False


def get_user_access_info(user_id: int) -> Dict[str, Any]:
    """Возвращает информацию о доступе пользователя"""
    user_id_str = str(user_id)

    if user_id in ADMIN_IDS:
        return {
            'has_access': True,
            'is_admin': True,
            'access_until': 'Бессрочно (админ)'
        }

    if user_id_str not in users_data:
        return {
            'has_access': False,
            'is_admin': False,
            'access_until': None
        }

    user_data = users_data[user_id_str]
    access_info = {
        'has_access': True,
        'is_admin': False,
        'access_granted': user_data.get('access_granted'),
        'access_until': user_data.get('access_until')
    }

    # Проверяем срок действия и форматируем дату
    if 'access_until' in user_data:
        try:
            access_until = datetime.fromisoformat(user_data['access_until'])
            if datetime.now() >= access_until:
                access_info['has_access'] = False
                # Автоматически удаляем просроченный доступ
                del users_data[user_id_str]
                save_users_data(users_data)
            else:
                # Форматируем дату для читаемости
                access_info['access_until'] = access_until.strftime('%d.%m.%Y %H:%M')
        except ValueError:
            access_info['has_access'] = False

    # Форматируем дату выдачи доступа
    if 'access_granted' in user_data:
        try:
            access_granted = datetime.fromisoformat(user_data['access_granted'])
            access_info['access_granted'] = access_granted.strftime('%d.%m.%Y %H:%M')
        except ValueError:
            pass

    return access_info


# Определяем состояния (этапы анкеты)
class Form(StatesGroup):
    start = State()
    user_type = State()
    recruiter_email = State()
    recruiter_password = State()
    recruiter_time_slots = State()
    full_name = State()
    experience = State()
    skills = State()
    salary_expectations = State()
    relocation = State()
    final = State()
    scheduling = State()


# Состояния для админ-панели
class AdminStates(StatesGroup):
    waiting_for_user_id_grant = State()  # Для выдачи доступа
    waiting_for_user_id_revoke = State()  # Для отзыва доступа
    waiting_for_user_id_check = State()   # Для проверки доступа
    waiting_for_duration = State()


# Клавиатура для выбора типа пользователя
user_type_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Я кандидат"), KeyboardButton(text="Я рекрутер")]
    ],
    resize_keyboard=True
)

# Клавиатура для да/нет
yes_no_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Да"), KeyboardButton(text="Нет")],
        [KeyboardButton(text="Не знаю")]
    ],
    resize_keyboard=True
)

# Клавиатура для записи на собеседование
schedule_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Записаться на собеседование")],
        [KeyboardButton(text="Завершить диалог")]
    ],
    resize_keyboard=True
)

# Клавиатура для рекрутера (пропустить ввод слотов)
skip_slots_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Пропустить и использовать стандартные слоты")],
        [KeyboardButton(text="Ввести свои слоты")]
    ],
    resize_keyboard=True
)

# Клавиатура для админ-панели
admin_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="👥 Список пользователей")],
        [KeyboardButton(text="➕ Выдать доступ"), KeyboardButton(text="❌ Отозвать доступ")],
        [KeyboardButton(text="⏰ Проверить доступ"), KeyboardButton(text="📧 Тест email")],
        [KeyboardButton(text="🔙 Выход")]
    ],
    resize_keyboard=True
)


# Функция для проверки email
def is_valid_email(email: str) -> bool:
    """Проверяет валидность email адреса"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None


# Функция для создания iCalendar события
def create_ical_event(candidate_name: str, interview_time: str, candidate_data: Dict[str, str]) -> str:
    """Создает iCalendar событие для добавления в календарь"""
    try:
        # Парсим дату и время (обрабатываем оба формата)
        try:
            # Пробуем первый формат: "25.09.2025 в 14:00"
            dt = datetime.strptime(interview_time, '%d.%m.%Y в %H:%M')
        except ValueError:
            try:
                # Пробуем второй формат: "25.09.2025 14:00"
                dt = datetime.strptime(interview_time, '%d.%m.%Y %H:%M')
            except ValueError as e:
                logger.error(f"❌ Неизвестный формат времени: {interview_time}")
                raise e

        end_time = dt + timedelta(hours=1)

        # Создаем iCalendar вручную
        ical_content = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//HR AI Bot//EN
CALSCALE:GREGORIAN
METHOD:REQUEST
BEGIN:VEVENT
UID:{datetime.now().strftime('%Y%m%dT%H%M%S')}@hrbot.com
SUMMARY:Собеседование: {candidate_name}
DTSTART:{dt.strftime('%Y%m%dT%H%M%S')}
DTEND:{end_time.strftime('%Y%m%dT%H%M%S')}
DTSTAMP:{datetime.now().strftime('%Y%m%dT%H%M%S')}
ORGANIZER;CN=HR AI Bot:mailto:noreply@hrbot.com
DESCRIPTION:Кандидат: {candidate_name}\\nОпыт: {candidate_data.get('experience', 'Не указан')[:100]}\\nНавыки: {candidate_data.get('skills', 'Не указаны')[:100]}
LOCATION:Онлайн созвон
STATUS:CONFIRMED
SEQUENCE:0
BEGIN:VALARM
ACTION:DISPLAY
DESCRIPTION:Напоминание о собеседовании
TRIGGER:-PT30M
END:VALARM
END:VEVENT
END:VCALENDAR"""

        logger.info("✅ iCalendar событие создано успешно")
        return ical_content

    except Exception as e:
        logger.error(f"❌ Ошибка при создании iCalendar события: {e}")
        return ""


# Функция для создания ссылки Google Calendar
def create_google_calendar_link(candidate_name: str, interview_time: str, candidate_data: Dict[str, str]) -> str:
    """Создает ссылку для добавления события в Google Calendar"""
    try:
        logger.info(f"🔄 Создание Google Calendar ссылки для: {candidate_name}, время: {interview_time}")

        # Парсим дату и время (обрабатываем оба формата)
        try:
            # Пробуем первый формат: "25.09.2025 в 14:00"
            dt = datetime.strptime(interview_time, '%d.%m.%Y в %H:%M')
            logger.info("✅ Формат времени распознан: 'дд.мм.гггг в чч:мм'")
        except ValueError:
            try:
                # Пробуем второй формат: "25.09.2025 14:00"
                dt = datetime.strptime(interview_time, '%d.%m.%Y %H:%M')
                logger.info("✅ Формат времени распознан: 'дд.мм.гггг чч:мм'")
            except ValueError as e:
                logger.error(f"❌ Неизвестный формат времени: {interview_time}")
                logger.error(f"Ошибка: {e}")
                return ""

        end_time = dt + timedelta(hours=1)
        logger.info(f"✅ Время начала: {dt}, время окончания: {end_time}")

        # Форматируем даты для URL (правильный формат для Google Calendar)
        start_str = dt.strftime('%Y%m%dT%H%M%S')
        end_str = end_time.strftime('%Y%m%dT%H%M%S')
        logger.info(f"✅ Время для URL: {start_str} - {end_str}")

        # Создаем описание
        description = f"Кандидат: {candidate_name}\n"
        description += f"Опыт: {candidate_data.get('experience', 'Не указан')}\n"
        description += f"Навыки: {candidate_data.get('skills', 'Не указаны')}\n"
        description += f"Ожидания ЗП: {candidate_data.get('salary_expectations', 'Не указаны')}\n"
        description += f"Переезд: {candidate_data.get('relocation', 'Не указана')}"

        # Кодируем параметры для URL
        title = urllib.parse.quote(f"Собеседование: {candidate_name}")
        details = urllib.parse.quote(description)
        location = urllib.parse.quote("Онлайн созвон")

        # Создаем правильную ссылку Google Calendar
        url = f"https://calendar.google.com/calendar/render?action=TEMPLATE"
        url += f"&text={title}"
        url += f"&dates={start_str}/{end_str}"
        url += f"&details={details}"
        url += f"&location={location}"
        url += f"&sf=true&output=xml"

        logger.info(f"✅ Создана ссылка Google Calendar: {url[:100]}...")
        return url

    except Exception as e:
        logger.error(f"❌ Ошибка при создании ссылки Google Calendar: {e}")
        logger.error(f"Трассировка: {traceback.format_exc()}")
        return ""


# Функция для отправки email только со ссылкой на календарь
async def send_calendar_link_email(to_email: str, candidate_name: str, interview_time: str,
                                   calendar_link: str, from_email: str, password: str) -> bool:
    """
    Отправляет отдельное письмо только со ссылкой на Google Calendar
    """
    try:
        subject = f"📅 Ссылка на собеседование: {candidate_name}"

        body = f"""Уважаемый рекрутер!

Для удобства отправляем отдельную ссылку для добавления собеседования в Google Calendar.

👤 Кандидат: {candidate_name}
📅 Время: {interview_time}

📎 Ссылка для добавления в Google Calendar:
{calendar_link}

Просто перейдите по ссылке и нажмите "Сохранить" чтобы добавить событие в ваш календарь.

С уважением,
HR AI Bot
"""

        # Создаем простое текстовое письмо
        msg = MIMEMultipart()
        msg['From'] = from_email
        msg['To'] = to_email
        msg['Subject'] = subject

        # Добавляем текстовую часть
        msg.attach(MIMEText(body, 'plain', 'utf-8'))

        # Настройка SMTP для Gmail
        smtp_server = "smtp.gmail.com"
        smtp_port = 587

        # Отправка письма
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(from_email, password)
            server.sendmail(from_email, to_email, msg.as_string())
            server.quit()

        logger.info(f"✅ Email со ссылкой на календарь отправлен на: {to_email}")
        return True

    except Exception as e:
        logger.error(f"❌ Ошибка при отправке email со ссылкой на календарь: {e}")
        return False


# Функция для отправки email с вложением календаря
async def send_email_with_calendar(to_email: str, subject: str, body: str,
                                   from_email: str, password: str,
                                   ical_content: str, candidate_name: str) -> bool:
    """
    Отправляет email с прикрепленным календарным событием
    """
    try:
        logger.info(f"🔄 Попытка отправки email на {to_email} от {from_email}")

        # Создаем multipart сообщение
        msg = MIMEMultipart('mixed')
        msg['From'] = from_email
        msg['To'] = to_email
        msg['Subject'] = subject

        # Добавляем текстовую часть
        text_part = MIMEText(body, 'plain', 'utf-8')
        msg.attach(text_part)

        # Добавляем календарное событие как вложение (если есть)
        if ical_content:
            ical_part = MIMEText(ical_content, 'calendar')
            ical_part.add_header('Content-Type', 'text/calendar; charset="utf-8"; method=REQUEST')
            ical_part.add_header('Content-Disposition', 'attachment; filename="interview.ics"')
            ical_part.add_header('Content-Class', 'urn:content-classes:calendarmessage')
            msg.attach(ical_part)
            logger.info("✅ iCalendar вложение добавлено")

        # Настройка SMTP для Gmail
        smtp_server = "smtp.gmail.com"
        smtp_port = 587

        logger.info(f"🔗 Подключение к SMTP серверу {smtp_server}:{smtp_port}")

        # Отправка письма
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.ehlo()
            logger.info("✅ EHLO выполнено")

            server.starttls()
            logger.info("✅ STARTTLS выполнено")

            server.ehlo()
            logger.info("✅ EHLO после STARTTLS выполнено")

            logger.info(f"🔐 Попытка авторизации для {from_email}")
            server.login(from_email, password)
            logger.info("✅ Авторизация успешна")

            logger.info(f"📤 Отправка письма на {to_email}")
            server.sendmail(from_email, to_email, msg.as_string())
            logger.info("✅ Письмо отправлено")

            server.quit()
            logger.info("✅ SMTP соединение закрыто")

        logger.info(f"✅ Email успешно отправлен на: {to_email}")
        return True

    except smtplib.SMTPAuthenticationError as e:
        logger.error(f"❌ Ошибка авторизации SMTP: {e}")
        logger.error("Проверьте пароль приложения и включена ли двухфакторная аутентификация")
        return False
    except smtplib.SMTPException as e:
        logger.error(f"❌ Ошибка SMTP: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ Неожиданная ошибка при отправке email: {e}")
        return False


# Функция для отправки уведомления рекрутеру
async def send_recruiter_notification(recruiter_data: Dict[str, str], candidate_data: Dict[str, str],
                                      interview_time: str = None) -> bool:
    """
    Отправляет уведомление рекрутеру о новом кандидате с календарным событием
    """
    try:
        recruiter_email = recruiter_data.get('email', '')
        from_email = recruiter_data.get('email_login', '')
        password = recruiter_data.get('email_password', '')

        logger.info(f"🔄 Подготовка уведомления для рекрутера {recruiter_email}")

        if not all([recruiter_email, from_email, password]):
            logger.error("❌ Не все данные email настроены")
            logger.error(
                f"recruiter_email: {recruiter_email}, from_email: {from_email}, password: {'*' * len(password) if password else 'None'}")
            return False

        candidate_name = candidate_data.get('full_name', 'Неизвестный кандидат')
        experience = candidate_data.get('experience', 'Не указан')
        skills = candidate_data.get('skills', 'Не указаны')
        salary = candidate_data.get('salary_expectations', 'Не указаны')
        relocation = candidate_data.get('relocation', 'Не указана')
        ai_analysis = candidate_data.get('ai_analysis', 'Анализ не выполнен')
        deep_analysis = candidate_data.get('deep_analysis', {})

        # Создаем тему и тело письма
        subject = f"✅ Новый кандидат: {candidate_name}"
        if interview_time:
            subject = f"✅ Кандидат записался на собеседование: {candidate_name}"

        body = f"""Уважаемый рекрутер!

Новый кандидат заполнил анкету через HR AI Bot.

📋 ДАННЫЕ КАНДИДАТА:
👤 Имя: {candidate_name}
💼 Опыт работы: {experience[:300]}{'...' if len(experience) > 300 else ''}
🛠 Навыки: {skills[:300]}{'...' if len(skills) > 300 else ''}
💰 Ожидания по ЗП: {salary}
🚗 Готовность к переезду: {relocation}

"""

        ical_content = ""
        calendar_link = ""
        email_sent = False
        calendar_email_sent = False

        if interview_time:
            body += f"📅 Время собеседования: {interview_time}\n\n"

            # Создаем iCalendar событие
            logger.info(f"🔄 Создание iCalendar события для {interview_time}")
            ical_content = create_ical_event(candidate_name, interview_time, candidate_data)

            # Создаем ссылку Google Calendar
            logger.info(f"🔄 Создание ссылки Google Calendar для {interview_time}")
            calendar_link = create_google_calendar_link(candidate_name, interview_time, candidate_data)

            if ical_content:
                logger.info("✅ iCalendar событие создано успешно")
                body += "📅 Календарное событие прикреплено к этому письму. Вы можете добавить его в свой календарь.\n\n"
            else:
                logger.error("❌ Не удалось создать iCalendar событие")

            if calendar_link:
                logger.info("✅ Ссылка Google Calendar создана успешно")
                body += f"📎 Ссылка для добавления в Google Calendar: {calendar_link}\n\n"
            else:
                logger.error("❌ Не удалось создать ссылку Google Calendar")

            # Отправляем основное письмо с данными кандидата
            logger.info("🔄 Отправка основного письма с данными кандидата")
            email_sent = await send_email_with_calendar(
                to_email=recruiter_email,
                subject=subject,
                body=body,
                from_email=from_email,
                password=password,
                ical_content=ical_content,
                candidate_name=candidate_name
            )

            if email_sent:
                logger.info("✅ Основное письмо отправлено успешно")
            else:
                logger.error("❌ Не удалось отправить основное письмо")

            # ОТДЕЛЬНО отправляем письмо со ссылкой на календарь
            if calendar_link and email_sent:
                logger.info("🔄 Отправка отдельного письма со ссылкой на календарь")
                calendar_email_sent = await send_calendar_link_email(
                    to_email=recruiter_email,
                    candidate_name=candidate_name,
                    interview_time=interview_time,
                    calendar_link=calendar_link,
                    from_email=from_email,
                    password=password
                )

                if calendar_email_sent:
                    logger.info("✅ Отдельное письмо со ссылкой на календарь отправлено")
                else:
                    logger.error("❌ Не удалось отправить письмо со ссылкой на календарь")
            else:
                logger.warning("⚠️ Пропуск отправки письма со ссылкой: нет ссылки или основное письмо не отправлено")

        else:
            # Если нет времени собеседования, отправляем простое уведомление
            body += f"""🤖 AI-АНАЛИЗ:
{ai_analysis}

"""

            # Добавляем глубокий анализ, если он есть
            if deep_analysis and isinstance(deep_analysis, dict):
                body += f"""🎯 ОЦЕНКА КАНДИДАТА: {deep_analysis.get('score', 'N/A')}

🌟 СИЛЬНЫЕ СТОРОНЫ:
""" + "\n".join([f"• {s}" for s in deep_analysis.get('strengths', [])]) + f"""

📉 СЛАБЫЕ СТОРОНЫ:
""" + "\n".join([f"• {w}" for w in deep_analysis.get('weaknesses', [])]) + f"""

❓ ВОПРОСЫ ДЛЯ ИНТЕРВЬЮ:
""" + "\n".join([f"{i + 1}. {q}" for i, q in enumerate(deep_analysis.get('interview_questions', []))]) + f"""

💡 РЕКОМЕНДАЦИИ:
""" + "\n".join([f"• {r}" for r in deep_analysis.get('recommendations', [])]) + f"""

"""

            body += f"""📅 Дата заполнения анкеты: {datetime.now().strftime('%d.%m.%Y %H:%M')}

Все данные кандидата сохранены в системе HR AI Bot.

С уважением,
HR AI Bot
© {datetime.now().year} Ваша компания
"""

            # Отправляем email без календаря
            logger.info("🔄 Отправка письма без календаря (только данные кандидата)")
            email_sent = await send_email_with_calendar(
                to_email=recruiter_email,
                subject=subject,
                body=body,
                from_email=from_email,
                password=password,
                ical_content="",
                candidate_name=candidate_name
            )

            if email_sent:
                logger.info("✅ Письмо с данными кандидата отправлено успешно")
            else:
                logger.error("❌ Не удалось отправить письмо с данными кандидата")

        return email_sent

    except Exception as e:
        logger.error(f"❌ Критическая ошибка при подготовке уведомления: {e}")
        logger.error(f"Трассировка: {traceback.format_exc()}")
        return False


# Функция для глубокого анализа кандидата с помощью AI
async def analyze_candidate_deep(answers: Dict[str, str]) -> Dict[str, str]:
    """Глубокий анализ кандидата с оценкой и вопросами для интервью"""
    if not OPENAI_API_KEY or OPENAI_API_KEY == "ВАШ_OPENAI_API_KEY":
        return generate_local_deep_analysis(answers)

    prompt = f"""
Проанализируй данные кандидата и предоставь:
1. Оценку кандидата по шкале от 1 до 10 (где 10 - идеальный кандидат)
2. Краткое резюме сильных и слабых сторон
3. 5-7 ключевых вопросов для технического интервью
4. Рекомендации по дальнейшим шагам

ДАННЫЕ КАНДИДАТА:
- Имя: {answers.get('full_name', 'Не указано')}
- Опыт работы: {answers.get('experience', 'Не указан')}
- Ключевые навыки: {answers.get('skills', 'Не указаны')}
- Ожидания по зарплате: {answers.get('salary_expectations', 'Не указаны')}
- Готовность к переезду: {answers.get('relocation', 'Не указана')}

Верни ответ в формате JSON:
{{
  "score": "8/10",
  "strengths": ["сильная сторона 1", "сильная сторона 2"],
  "weaknesses": ["слабая сторона 1", "слабая сторона 2"],
  "interview_questions": ["вопрос 1", "вопрос 2", "вопрос 3"],
  "recommendations": ["рекомендация 1", "рекомендация 2"]
}}
"""

    try:
        response = openai.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=800,
            temperature=0.7,
            timeout=30
        )

        analysis_text = response.choices[0].message.content.strip()

        # Пытаемся распарсить JSON ответ
        try:
            analysis_data = json.loads(analysis_text)
            return analysis_data
        except json.JSONDecodeError:
            # Если не удалось распарсить JSON, возвращаем текстовый анализ
            return {
                "score": "N/A",
                "strengths": ["Не удалось проанализировать"],
                "weaknesses": ["Не удалось проанализировать"],
                "interview_questions": ["Расскажите о вашем опыте работы?", "Какие технологии вы используете?"],
                "recommendations": ["Провести техническое интервью"],
                "raw_analysis": analysis_text
            }

    except Exception as e:
        logger.error(f"Ошибка при глубоком анализе ответов OpenAI: {e}")
        return generate_local_deep_analysis(answers)


def generate_local_deep_analysis(answers: Dict[str, str]) -> Dict[str, Any]:
    """Локальный глубокий анализ ответов без OpenAI"""
    name = answers.get('full_name', 'Кандидат')
    experience = answers.get('experience', 'не указан')
    skills = answers.get('skills', 'не указаны')
    salary = answers.get('salary_expectations', 'не указаны')
    relocation = answers.get('relocation', 'не указана')

    return {
        "score": "7/10",
        "strengths": [
            "Предоставил подробную информацию об опыте",
            "Указал конкретные навыки и технологии"
        ],
        "weaknesses": [
            "Требуется уточнение по коммерческому опыту",
            "Необходимо проверить реальные навыки"
        ],
        "interview_questions": [
            "Расскажите о вашем последнем проекте?",
            "Какие технологии вы использовали в работе?",
            "Как вы решали сложные технические задачи?",
            "Каков ваш опыт работы в команде?",
            "Что вы знаете о наших продуктах/услугах?"
        ],
        "recommendations": [
            "Провести техническое интервью",
            "Проверить рекомендации с предыдущих мест работы"
        ]
    }


# Функция для анализа ответов с помощью AI
async def analyze_answers(answers: Dict[str, str]) -> str:
    """Анализирует ответы кандидата с помощью OpenAI API"""
    if not OPENAI_API_KEY or OPENAI_API_KEY == "ВАШ_OPENAI_API_KEY":
        return generate_local_analysis(answers)

    prompt = f"""
Проанализируй ответы кандидата и составь краткую сводку (3-4 предложения) на русском языке.
Выдели ключевые моменты: опыт, основные навыки, адекватность ожиданий по зарплате, готовность к переезду.

Данные кандидата:
- Имя: {answers.get('full_name', 'Не указано')}
- Опыт работы: {answers.get('experience', 'Не указан')}
- Ключевые навыки: {answers.get('skills', 'Не указаны')}
- Ожидания по зарплате: {answers.get('salary_expectations', 'Не указаны')}
- Готовность к переезду: {answers.get('relocation', 'Не указана')}
"""

    try:
        response = openai.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0.7,
            timeout=30
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Ошибка при анализе ответов OpenAI: {e}")
        return generate_local_analysis(answers)


def generate_local_analysis(answers: Dict[str, str]) -> str:
    """Локальный анализ ответов без OpenAI"""
    name = answers.get('full_name', 'Кандидат')
    experience = answers.get('experience', 'не указан')
    skills = answers.get('skills', 'не указаны')
    salary = answers.get('salary_expectations', 'не указаны')
    relocation = answers.get('relocation', 'не указана')

    return (f"{name} предоставил подробные ответы. "
            f"Опыт работы: {experience[:100]}... "
            f"Ключевые навыки: {skills[:100]}... "
            f"Ожидания по ЗП: {salary}. "
            f"Готовность к переезду: {relocation}.")


# Генерация слотов для собеседования
def generate_time_slots(recruiter_id: str = None) -> List[str]:
    """Генерирует список свободных слотов на следующие 3 дня или использует слоты рекрутера"""
    # Проверяем, есть ли у рекрутера свои слоты
    if recruiter_id and recruiter_id in recruiters_data:
        recruiter_slots = recruiters_data[recruiter_id].get('time_slots', [])
        if recruiter_slots:
            # Конвертируем слоты рекрутера в единый формат
            formatted_slots = []
            for slot in recruiter_slots:
                try:
                    # Пробуем разные форматы и конвертируем в единый
                    try:
                        dt = datetime.strptime(slot, '%d.%m.%Y в %H:%M')
                    except ValueError:
                        dt = datetime.strptime(slot, '%d.%m.%Y %H:%M')
                    formatted_slots.append(dt.strftime('%d.%m.%Y в %H:%M'))
                except ValueError:
                    formatted_slots.append(slot)  # Оставляем как есть если не распарсилось
            return formatted_slots

    # Если слотов рекрутера нет, генерируем стандартные
    now = datetime.now()
    slots = []
    for day in range(1, 4):
        current_date = now + timedelta(days=day)
        date_str = current_date.strftime("%d.%m.%Y")
        for hour in [10, 12, 14, 16]:
            slot = f"{date_str} в {hour}:00"  # Единый формат
            slots.append(slot)
    return slots


# Мидлварь для проверки доступа
@dp.message.middleware()
async def access_check_middleware(handler, event: types.Message, data):
    # Пропускаем команды /start и /admin для всех
    if event.text and any(event.text.startswith(cmd) for cmd in ['/start', '/admin']):
        return await handler(event, data)

    # Проверяем доступ для всех остальных сообщений
    if not check_user_access(event.from_user.id):
        await event.answer(
            "❌ Доступ к боту ограничен.\n\n"
            "Обратитесь к администратору для получения доступа.\n"
            "Для связи используйте команду /start"
        )
        return

    return await handler(event, data)


# Обработчик команды /start
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id

    # Проверяем доступ
    if not check_user_access(user_id) and user_id not in ADMIN_IDS:
        await message.answer(
            "👋 Добро пожаловать!\n\n"
            "❌ Доступ к боту ограничен. Обратитесь к администратору для получения доступа.\n\n"
            "Если вы администратор, используйте команду /admin"
        )
        return

    await state.set_state(Form.user_type)
    await message.answer(
        "Добро пожаловать в HR AI-ассистент! 🤖\n\nПожалуйста, выберите, кто вы:",
        reply_markup=user_type_kb
    )


# Обработчик команды /admin
@dp.message(Command("admin"))
async def cmd_admin(message: types.Message, state: FSMContext):
    user_id = message.from_user.id

    if user_id not in ADMIN_IDS:
        await message.answer("❌ У вас нет прав администратора.")
        return

    await message.answer(
        "👨‍💼 Панель администратора\n\n"
        "Выберите действие:",
        reply_markup=admin_kb
    )


# Обработчики админ-панели
@dp.message(F.text == "🔙 Выход")
async def admin_exit(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return

    await state.clear()
    await message.answer(
        "Выход из панели администратора.",
        reply_markup=ReplyKeyboardRemove()
    )


@dp.message(F.text == "📊 Статистика")
async def admin_stats(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    total_users = len(users_data)
    total_candidates = len(candidates_data)
    total_recruiters = len(recruiters_data)

    # Считаем активных пользователей (с неистекшим доступом)
    active_users = 0
    for user_id, user_data in users_data.items():
        if 'access_until' in user_data:
            try:
                access_until = datetime.fromisoformat(user_data['access_until'])
                if datetime.now() < access_until:
                    active_users += 1
            except ValueError:
                continue

    stats_text = (
        f"📊 Статистика системы:\n\n"
        f"👥 Всего пользователей с доступом: {total_users}\n"
        f"✅ Активных пользователей: {active_users}\n"
        f"👤 Кандидатов: {total_candidates}\n"
        f"📋 Рекрутеров: {total_recruiters}"
    )

    await message.answer(stats_text)


@dp.message(F.text == "👥 Список пользователей")
async def admin_users_list(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    if not users_data:
        await message.answer("📝 Список пользователей пуст.")
        return

    users_list = "👥 Список пользователей с доступом:\n\n"

    for i, (user_id, user_data) in enumerate(list(users_data.items())[:50], 1):  # Ограничиваем вывод
        access_info = get_user_access_info(int(user_id))

        users_list += f"{i}. ID: {user_id}\n"
        users_list += f"   Доступ выдан: {user_data.get('access_granted', 'N/A')}\n"
        users_list += f"   Доступ до: {user_data.get('access_until', 'N/A')}\n"

        if access_info['has_access']:
            users_list += "   ✅ Активен\n"
        else:
            users_list += "   ❌ Истек\n"
        users_list += "\n"

    if len(users_data) > 50:
        users_list += f"\n... и еще {len(users_data) - 50} пользователей"

    await message.answer(users_list)


# Обработчик для выдачи доступа
@dp.message(F.text == "➕ Выдать доступ")
async def admin_grant_access(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return

    await state.set_state(AdminStates.waiting_for_user_id_grant)
    await message.answer(
        "Введите ID пользователя, которому нужно выдать доступ:\n\n"
        "ID можно получить переслав сообщение от пользователя боту @userinfobot",
        reply_markup=ReplyKeyboardRemove()
    )


# Обработчик для отзыва доступа
@dp.message(F.text == "❌ Отозвать доступ")
async def admin_revoke_access(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return

    await state.set_state(AdminStates.waiting_for_user_id_revoke)
    await message.answer(
        "Введите ID пользователя, у которого нужно отозвать доступ:",
        reply_markup=ReplyKeyboardRemove()
    )


# Обработчик для проверки доступа
@dp.message(F.text == "⏰ Проверить доступ")
async def admin_check_access(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return

    await state.set_state(AdminStates.waiting_for_user_id_check)
    await message.answer(
        "Введите ID пользователя для проверки доступа:",
        reply_markup=ReplyKeyboardRemove()
    )


# Обработчик ввода ID для выдачи доступа
@dp.message(AdminStates.waiting_for_user_id_grant)
async def admin_process_user_id_grant(message: types.Message, state: FSMContext):
    try:
        user_id = int(message.text.strip())
        await state.update_data(target_user_id=user_id)
        await state.set_state(AdminStates.waiting_for_duration)
        await message.answer(
            "Введите количество дней доступа (по умолчанию 30):",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text="30"), KeyboardButton(text="60"), KeyboardButton(text="90")],
                    [KeyboardButton(text="7"), KeyboardButton(text="14")]
                ],
                resize_keyboard=True
            )
        )
    except ValueError:
        await message.answer("❌ Неверный формат ID. Введите числовой ID:")


# Обработчик ввода ID для отзыва доступа
@dp.message(AdminStates.waiting_for_user_id_revoke)
async def admin_process_user_id_revoke(message: types.Message, state: FSMContext):
    try:
        user_id = int(message.text.strip())

        if revoke_access(user_id):
            await message.answer(
                f"✅ Доступ отозван у пользователя {user_id}.",
                reply_markup=admin_kb
            )
        else:
            await message.answer(
                f"❌ Ошибка при отзыве доступа у пользователя {user_id}.",
                reply_markup=admin_kb
            )

        await state.clear()

    except ValueError:
        await message.answer("❌ Неверный формат ID. Введите числовой ID:")


# Обработчик ввода ID для проверки доступа
@dp.message(AdminStates.waiting_for_user_id_check)
async def admin_process_user_id_check(message: types.Message, state: FSMContext):
    try:
        user_id = int(message.text.strip())
        access_info = get_user_access_info(user_id)

        if access_info['has_access']:
            status = "✅ Активен"
            until_text = f"⏰ Действует до: {access_info.get('access_until', 'N/A')}"
        else:
            status = "❌ Не активен"
            until_text = ""

        response = (
            f"👤 Пользователь ID: {user_id}\n"
            f"📊 Статус: {status}\n"
            f"{until_text}\n"
            f"📅 Доступ выдан: {access_info.get('access_granted', 'N/A')}\n"
            f"👑 Админ: {'Да' if access_info.get('is_admin') else 'Нет'}"
        )

        await message.answer(response, reply_markup=admin_kb)
        await state.clear()

    except ValueError:
        await message.answer("❌ Неверный формат ID. Введите числовой ID:")


# Обработчик ввода длительности доступа
@dp.message(AdminStates.waiting_for_duration)
async def admin_process_duration(message: types.Message, state: FSMContext):
    try:
        duration = int(message.text.strip())
        user_data = await state.get_data()
        target_user_id = user_data['target_user_id']

        if grant_access(target_user_id, duration):
            access_info = get_user_access_info(target_user_id)
            await message.answer(
                f"✅ Доступ выдан пользователю {target_user_id} на {duration} дней.\n\n"
                f"Доступ действителен до: {access_info['access_until']}",
                reply_markup=admin_kb
            )
        else:
            await message.answer(
                "❌ Ошибка при выдаче доступа.",
                reply_markup=admin_kb
            )

        await state.clear()

    except ValueError:
        await message.answer("❌ Неверный формат. Введите число дней:")


@dp.message(F.text == "📧 Тест email")
async def admin_test_email(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    # Ищем первого рекрутера для теста
    if not recruiters_data:
        await message.answer("❌ Нет зарегистрированных рекрутеров для теста.")
        return

    recruiter_id = next(iter(recruiters_data))
    recruiter_data = recruiters_data[recruiter_id]

    test_candidate_data = {
        'full_name': 'Тестовый Кандидат',
        'experience': '5 лет разработки Python',
        'skills': 'Python, Django, PostgreSQL',
        'salary_expectations': '150 000 руб.',
        'relocation': 'Да'
    }

    await message.answer("🔄 Отправка тестового email...")

    success = await send_recruiter_notification(
        recruiter_data,
        test_candidate_data,
        "25.12.2024 в 14:00"
    )

    if success:
        await message.answer("✅ Тестовый email отправлен успешно!")
    else:
        await message.answer("❌ Ошибка отправки тестового email. Проверьте логи.")


# Обработчик выбора типа пользователя
@dp.message(Form.user_type)
async def process_user_type(message: types.Message, state: FSMContext):
    if message.text == "Я кандидат":
        await state.set_state(Form.full_name)
        await message.answer(
            "Отлично! Я проведу первичный скрининг по вакансии. Это займет 3-5 минут.\n\n"
            "Для начала, как вас зовут? (ФИО или имя)",
            reply_markup=ReplyKeyboardRemove()
        )
    elif message.text == "Я рекрутер":
        await state.set_state(Form.recruiter_email)
        await message.answer(
            "Добро пожаловать в панель рекрутера! 📊\n\n"
            "Пожалуйста, введите ваш Gmail адрес для получения уведомлений о новых кандидатах:",
            reply_markup=ReplyKeyboardRemove()
        )
    else:
        await message.answer("Пожалуйста, выберите вариант с помощью кнопки ниже.", reply_markup=user_type_kb)


# Обработчик ввода email рекрутера
@dp.message(Form.recruiter_email)
async def process_recruiter_email(message: types.Message, state: FSMContext):
    email = message.text.strip()

    if not is_valid_email(email):
        await message.answer("Пожалуйста, введите корректный Gmail адрес (например: example@gmail.com):")
        return

    if not email.endswith('@gmail.com'):
        await message.answer("Пожалуйста, используйте Gmail адрес (оканчивается на @gmail.com):")
        return

    await state.update_data(recruiter_email=email, email_login=email)
    await state.set_state(Form.recruiter_password)

    await message.answer(
        "Теперь введите пароль приложения для Gmail:\n\n"
        "Как получить пароль приложения:\n"
        "1. Войдите в ваш Gmail аккаунт\n"
        "2. Включите двухфакторную аутентификацию\n"
        "3. Перейдите в Настройки → Безопасность → Пароли приложений\n"
        "4. Создайте пароль для 'Почты'\n"
        "5. Введите полученный пароль ниже:",
        reply_markup=ReplyKeyboardRemove()
    )


# Обработчик ввода пароля рекрутера
@dp.message(Form.recruiter_password)
async def process_recruiter_password(message: types.Message, state: FSMContext):
    email_password = message.text.strip()

    if not email_password:
        await message.answer("Пожалуйста, введите пароль приложения:")
        return

    user_data = await state.get_data()
    recruiter_email = user_data.get('recruiter_email', '')

    # Сохраняем данные рекрутера
    recruiters_data[str(message.from_user.id)] = {
        'email': recruiter_email,
        'email_login': recruiter_email,
        'email_password': email_password,
        'registration_date': datetime.now().isoformat()
    }
    save_recruiters_data(recruiters_data)

    logger.info(f"Рекрутер {message.from_user.id} зарегистрировал email: {recruiter_email}")

    # Тестовое письмо для проверки
    test_sent = await send_email_with_calendar(
        to_email=recruiter_email,
        subject="✅ Тестовое уведомление от HR AI Bot",
        body="Это тестовое уведомление. Если вы получили это письмо, значит настройки email работают корректно.",
        from_email=recruiter_email,
        password=email_password,
        ical_content="",
        candidate_name="Test"
    )

    if test_sent:
        await state.set_state(Form.recruiter_time_slots)
        await message.answer(
            f"✅ Настройки email успешно сохранены!\n"
            f"📧 Тестовое письмо отправлено на: {recruiter_email}\n\n"
            f"Теперь вы можете указать свои доступные временные слоты для созвона с кандидатами.\n\n"
            f"Введите 5 временных слотов в формате:\n"
            f"ДД.ММ.ГГГГ ЧЧ:00\n\n"
            f"Например:\n"
            f"15.12.2023 10:00\n"
            f"15.12.2023 14:00\n"
            f"16.12.2023 11:00\n"
            f"16.12.2023 15:00\n"
            f"17.12.2023 12:00\n\n"
            f"Или нажмите кнопку ниже, чтобы использовать стандартные слоты:",
            reply_markup=skip_slots_kb
        )
    else:
        await message.answer(
            f"⚠️ Не удалось отправить тестовое письмо.\n"
            f"Проверьте правильность пароля приложения и настройки Gmail.\n\n"
            f"Попробуйте снова: /start",
            reply_markup=ReplyKeyboardRemove()
        )


# Обработчик ввода временных слотов рекрутером
@dp.message(Form.recruiter_time_slots)
async def process_recruiter_time_slots(message: types.Message, state: FSMContext):
    if message.text == "Пропустить и использовать стандартные слоты":
        await message.answer(
            "✅ Используются стандартные временные слоты.\n\n"
            "Теперь вы будете получать уведомления о новых кандидатах.\n\n"
            "Используйте команду /stats для просмотра статистики.",
            reply_markup=ReplyKeyboardRemove()
        )
        await state.clear()
        return

    if message.text == "Ввести свои слоты":
        await message.answer(
            "Введите 5 временных слотов в формате:\n"
            f"ДД.ММ.ГГГГ ЧЧ:00\n\n"
            f"Например:\n"
            f"15.12.2023 10:00\n"
            f"15.12.2023 14:00\n"
            f"16.12.2023 11:00\n"
            f"16.12.2023 15:00\n"
            f"17.12.2023 12:00",
            reply_markup=ReplyKeyboardRemove()
        )
        return

    # Парсим введенные слоты
    slots = message.text.strip().split('\n')
    if len(slots) != 5:
        await message.answer("Пожалуйста, введите ровно 5 временных слотов (по одному на строку):")
        return

    # Проверяем формат каждого слота
    valid_slots = []
    for slot in slots:
        slot = slot.strip()
        try:
            # Пытаемся распарсить дату и время
            datetime.strptime(slot, '%d.%m.%Y %H:%M')
            valid_slots.append(slot)
        except ValueError:
            await message.answer(f"Неверный формат: {slot}. Используйте формат ДД.ММ.ГГГГ ЧЧ:MM")
            return

    # Сохраняем слоты рекрутера
    recruiter_id = str(message.from_user.id)
    if recruiter_id in recruiters_data:
        recruiters_data[recruiter_id]['time_slots'] = valid_slots
        save_recruiters_data(recruiters_data)

    await message.answer(
        f"✅ Ваши временные слоты сохранены:\n\n" + "\n".join([f"• {slot}" for slot in valid_slots]) +
        f"\n\nТеперь кандидаты смогут выбирать из этих слотов для записи на собеседование.\n\n"
        f"Используйте команду /stats для просмотра статистики.",
        reply_markup=ReplyKeyboardRemove()
    )
    await state.clear()


# Обработчик имени кандидата
@dp.message(Form.full_name)
async def process_name(message: types.Message, state: FSMContext):
    await state.update_data(full_name=message.text)
    await state.set_state(Form.experience)
    await message.answer(
        f"Приятно познакомиться, {message.text}! 😊\n"
        f"Расскажите, пожалуйста, о вашем опыте работы:",
        reply_markup=ReplyKeyboardRemove()
    )


# Обработчик опыта работы
@dp.message(Form.experience)
async def process_experience(message: types.Message, state: FSMContext):
    # Проверяем, что это не команды выбора типа пользователя
    if message.text in ["Я кандидат", "Я рекрутер"]:
        # Если пользователь случайно нажал не ту кнопку, возвращаем его к выбору типа
        await state.set_state(Form.user_type)
        await message.answer("Пожалуйста, выберите вариант с помощью кнопок ниже.", reply_markup=user_type_kb)
        return

    await state.update_data(experience=message.text)
    await state.set_state(Form.skills)
    await message.answer(
        "Отлично! Теперь расскажите о ваших ключевых навыки:",
        reply_markup=ReplyKeyboardRemove()
    )


# Обработчик навыков
@dp.message(Form.skills)
async def process_skills(message: types.Message, state: FSMContext):
    # Проверяем, что это не команды выбора типа пользователя
    if message.text in ["Я кандидат", "Я рекрутер"]:
        # Если пользователь случайно нажал не ту кнопку, возвращаем его к выбору типа
        await state.set_state(Form.user_type)
        await message.answer("Пожалуйста, выберите вариант с помощью кнопок ниже.", reply_markup=user_type_kb)
        return

    await state.update_data(skills=message.text)
    await state.set_state(Form.salary_expectations)
    await message.answer(
        "Хорошо. Какие у вас ожидания по заработной плате?",
        reply_markup=ReplyKeyboardRemove()
    )


# Обработчик зарплатных ожиданий
@dp.message(Form.salary_expectations)
async def process_salary(message: types.Message, state: FSMContext):
    # Проверяем, что это не команды выбора типа пользователя
    if message.text in ["Я кандидат", "Я рекрутер"]:
        # Если пользователь случайно нажал не ту кнопку, возвращаем его к выбору типа
        await state.set_state(Form.user_type)
        await message.answer("Пожалуйста, выберите вариант с помощью кнопок ниже.", reply_markup=user_type_kb)
        return

    await state.update_data(salary_expectations=message.text)
    await state.set_state(Form.relocation)
    await message.answer(
        "Рассматриваете ли вы возможность переезда в другой город?",
        reply_markup=yes_no_kb
    )


# Обработчик готовности к переезду
@dp.message(Form.relocation)
async def process_relocation(message: types.Message, state: FSMContext):
    # Проверяем, что это не команды выбора типа пользователя
    if message.text in ["Я кандидат", "Я рекрутер"]:
        # Если пользователь случайно нажал не ту кнопку, возвращаем его к выбору типа
        await state.set_state(Form.user_type)
        await message.answer("Пожалуйста, выберите вариант с помощью кнопок ниже.", reply_markup=user_type_kb)
        return

    if message.text not in ["Да", "Нет", "Не знаю"]:
        await message.answer("Пожалуйста, ответьте с помощью кнопок ниже.", reply_markup=yes_no_kb)
        return

    await state.update_data(relocation=message.text)
    user_data = await state.get_data()
    user_data['user_id'] = message.from_user.id

    # Сохраняем данные
    candidates_data[str(message.from_user.id)] = user_data
    save_candidates_data(candidates_data)

    logger.info(f"Данные сохранены для user_id: {message.from_user.id}")

    await state.set_state(Form.final)
    await message.answer(
        "Спасибо за ответы! 📝\nИдет обработка вашей анкеты...",
        reply_markup=ReplyKeyboardRemove()
    )

    # Анализируем ответы с помощью AI
    analysis = await analyze_answers(user_data)
    candidates_data[str(message.from_user.id)]['ai_analysis'] = analysis
    save_candidates_data(candidates_data)

    # Глубокий анализ кандидата с оценкой и вопросами
    deep_analysis = await analyze_candidate_deep(user_data)
    candidates_data[str(message.from_user.id)]['deep_analysis'] = deep_analysis
    save_candidates_data(candidates_data)

    # Ищем активного рекрутера для отправки уведомления
    active_recruiter = None
    for recruiter_id in recruiters_data:
        if recruiters_data[recruiter_id].get('email'):
            active_recruiter = recruiter_id
            break

    if active_recruiter:
        # Отправляем уведомление рекрутеру
        notification_sent = await send_recruiter_notification(
            recruiters_data[active_recruiter],
            user_data
        )

        if notification_sent:
            logger.info(f"✅ Уведомление рекрутеру отправлено успешно")
            await message.answer(
                f"{hbold('Ваша анкета обработана:')}\n\n"
                f"{hitalic(analysis)}\n\n"
                f"Рекрутер получил уведомление о вашей анкете.",
                reply_markup=schedule_kb,
                parse_mode="HTML"
            )
        else:
            logger.warning(f"⚠️ Не удалось отправить уведомление рекрутеру")
            await message.answer(
                f"{hbold('Ваша анкета обработана:')}\n\n"
                f"{hitalic(analysis)}\n\n"
                f"Ваши данные сохранены в системе.",
                reply_markup=schedule_kb,
                parse_mode="HTML"
            )
    else:
        await message.answer(
            f"{hbold('Ваша анкета обработана:')}\n\n"
            f"{hitalic(analysis)}\n\n"
            f"Ваши данные сохранены в системе.",
            reply_markup=schedule_kb,
            parse_mode="HTML"
        )


# Обработчик кнопки записи на собеседование
@dp.message(Form.final, F.text == "Записаться на собеседование")
async def process_schedule_request(message: types.Message, state: FSMContext):
    await state.set_state(Form.scheduling)

    # Ищем активного рекрутера для получения его слотов
    active_recruiter = None
    for recruiter_id in recruiters_data:
        if recruiters_data[recruiter_id].get('email'):
            active_recruiter = recruiter_id
            break

    time_slots = generate_time_slots(active_recruiter)
    slots_text = "\n".join([f"{i + 1}. {slot}" for i, slot in enumerate(time_slots[:5])])

    await message.answer(
        f"Выберите удобный слот для собеседования:\n\n{slots_text}\n\n"
        f"Ответьте номером выбранного слота (1-5).",
        reply_markup=ReplyKeyboardRemove()
    )


# Обработчик выбора слота
@dp.message(Form.scheduling)
async def process_slot_selection(message: types.Message, state: FSMContext):
    try:
        slot_number = int(message.text)
        if 1 <= slot_number <= 5:
            # Ищем активного рекрутера для получения его слотов
            active_recruiter = None
            for recruiter_id in recruiters_data:
                if recruiters_data[recruiter_id].get('email'):
                    active_recruiter = recruiter_id
                    break

            time_slots = generate_time_slots(active_recruiter)
            selected_slot = time_slots[slot_number - 1]

            # Получаем данные кандидата
            user_data = candidates_data.get(str(message.from_user.id), {})
            user_data['interview_slot'] = selected_slot
            candidates_data[str(message.from_user.id)] = user_data
            save_candidates_data(candidates_data)

            # Ищем активного рекрутера
            if active_recruiter:
                # Отправляем уведомление о записи
                notification_sent = await send_recruiter_notification(
                    recruiters_data[active_recruiter],
                    user_data,
                    selected_slot
                )

                if notification_sent:
                    logger.info(f"✅ Уведомление отправлено успешно")
                else:
                    logger.error(f"❌ Ошибка отправки уведомления")

            await message.answer(
                f"✅ Вы записаны на собеседование: {selected_slot}\n\n"
                f"Календарное событие создано и отправлено рекрутеру.\n"
                f"С вами свяжутся для подтверждения.\n"
                f"Желаем удачи! 🍀",
                reply_markup=ReplyKeyboardRemove()
            )

            await state.clear()
        else:
            await message.answer("Пожалуйста, выберите номер от 1 до 5.")
    except ValueError:
        await message.answer("Пожалуйста, введите число от 1 до 5.")


# Обработчик завершения диалога
@dp.message(Form.final, F.text == "Завершить диалог")
async def process_finish(message: types.Message, state: FSMContext):
    await message.answer(
        "Диалог завершен. Спасибо за ваше время! Если передумаете, просто снова напишите /start.\n"
        "Ваши ответы сохранены и будут переданы рекрутеру.",
        reply_markup=ReplyKeyboardRemove()
    )
    await state.clear()


# Команда для просмотра статистики
@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    user_id = str(message.from_user.id)
    if user_id in recruiters_data:
        total_candidates = len(candidates_data)
        recruiter_slots = recruiters_data[user_id].get('time_slots', [])

        stats_text = f"📊 Статистика кандидатов:\n\n• Всего кандидатов: {total_candidates}"

        if recruiter_slots:
            stats_text += f"\n\n📅 Ваши временные слоты:\n" + "\n".join([f"• {slot}" for slot in recruiter_slots])
        else:
            stats_text += f"\n\n📅 Используются стандартные временные слоты"

        await message.answer(stats_text)
    else:
        await message.answer("Эта команда доступна только рекрутерам.")


# Команда для изменения временных слотов
@dp.message(Command("slots"))
async def cmd_slots(message: types.Message, state: FSMContext):
    user_id = str(message.from_user.id)
    if user_id in recruiters_data:
        await state.set_state(Form.recruiter_time_slots)
        await message.answer(
            "Введите 5 новых временных слотов в формате:\n"
            f"ДД.ММ.ГГГГ ЧЧ:00\n\n"
            f"Например:\n"
            f"15.12.2023 10:00\n"
            f"15.12.2023 14:00\n"
            f"16.12.2023 11:00\n"
            f"16.12.2023 15:00\n"
            f"17.12.2023 12:00\n\n"
            f"Или нажмите кнопку ниже, чтобы использовать стандартные слоты:",
            reply_markup=skip_slots_kb
        )
    else:
        await message.answer("Эта команда доступна только рекрутерам.")


# Запуск бота
async def main():
    logger.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())