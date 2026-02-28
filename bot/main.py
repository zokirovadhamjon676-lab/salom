from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from bot.config import BOT_TOKEN, ADMIN_ID, ADMIN_USERNAME
from bot.handlers.clients import (
    add_client_cmd, list_clients_handler,
    show_clients_for_delete, delete_client_callback
)
from bot.handlers.orders import (
    add_order_cmd, show_orders_for_delete, delete_order_callback
)
from bot.handlers.stats import export_orders_excel
from database.db import (
    add_client, add_order, get_clients,
    get_setting, set_setting, hash_password, check_password as db_check_password,
    add_user, get_user, update_user_phone_name, get_all_users,
    ban_user, unban_user, is_user_banned, delete_user
)
import random
import logging
import re

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

# Sessiyalar
reset_sessions = {}
change_phone_sessions = {}
change_password_sessions = {}
registration_sessions = {}
authenticated_users = set()

def send_sms_code(phone, code):
    logger.info(f"📱 SMS kod {phone} raqamiga yuborildi: {code}")
    return True

def is_admin(user_id):
    return user_id == ADMIN_ID

def main_menu(user_id=None):
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    # Asosiy tugmalar (barcha foydalanuvchilar uchun)
    buttons = [
        KeyboardButton("➕ Klient qo'shish"),
        KeyboardButton("📋 Klientlar ro'yxati"),
        KeyboardButton("🛍 Buyurtma qo'shish"),
        KeyboardButton("📊 Excel export"),
        KeyboardButton("🗑 O'chirish"),
        KeyboardButton("⚙️ Sozlamalar"),
        KeyboardButton("👤 Admin")  # Admin bilan bog'lanish tugmasi
    ]
    # Agar foydalanuvchi admin bo'lsa, yana bir tugma qo'shamiz
    if user_id and is_admin(user_id):
        buttons.append(KeyboardButton("👥 Foydalanuvchilar"))
    keyboard.add(*buttons)
    return keyboard

def authenticated_only(func):
    async def wrapper(message: types.Message):
        user_id = message.from_user.id
        if user_id not in authenticated_users:
            await message.answer("⚠️ Avval tizimga kiring. /start ni bosing.")
            return
        if is_user_banned(user_id):
            await message.answer("🚫 Siz bloklangansiz. Admin bilan bog‘laning.")
            return
        return await func(message)
    return wrapper

# -------------------- TEST BUYRUGLARI --------------------
@dp.message_handler(commands=['testadmin'])
async def test_admin(message: types.Message):
    await message.answer(
        f"Admin ID: {ADMIN_ID}\n"
        f"Sizning ID: {message.from_user.id}"
    )

@dp.message_handler(commands=['checkauth'])
async def check_auth(message: types.Message):
    if message.from_user.id in authenticated_users:
        await message.answer("✅ Siz autentifikatsiyadan o‘tgansiz")
    else:
        await message.answer("❌ Siz autentifikatsiyadan o‘tmagan")

# -------------------- START --------------------
@dp.message_handler(commands=['start'])
async def start_cmd(message: types.Message):
    user_id = message.from_user.id
    logger.info(f"User {user_id} started bot")

    if user_id in registration_sessions:
        await message.answer("Iltimos, avval ro'yxatdan o'tishni yakunlang.")
        return

    if user_id in reset_sessions:
        keyboard = InlineKeyboardMarkup()
        keyboard.add(InlineKeyboardButton("✅ Davom ettirish", callback_data="continue_reset"))
        keyboard.add(InlineKeyboardButton("❌ Bekor qilish", callback_data="cancel_reset"))
        await message.answer(
            "Sizda tugallanmagan parolni tiklash jarayoni bor. Davom ettirasizmi?",
            reply_markup=keyboard
        )
        return

    if user_id in authenticated_users:
        await message.answer("👋 Xush kelibsiz! CRM bot.", reply_markup=main_menu(user_id))
        return

    password_hash = get_setting("password_hash")
    admin_phone = get_setting("admin_phone")

    if not password_hash or not admin_phone:
        reset_sessions[user_id] = {'step': 'setup_phone'}
        await message.answer(
            "🤖 Bot birinchi marta ishga tushirilmoqda. Iltimos, sozlamalarni kiriting.\n"
            "Telefon raqamingizni xalqaro formatda yozing (masalan: +998901234567):"
        )
        return

    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("🔐 Kirish", callback_data="login"))
    keyboard.add(InlineKeyboardButton("❓ Parolni unutdingizmi?", callback_data="forgot_password"))
    await message.answer("🔒 Botdan foydalanish uchun tizimga kiring.", reply_markup=keyboard)

# -------------------- INLINE HANDLERLAR --------------------
@dp.callback_query_handler(lambda c: c.data == "login")
async def process_login(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.answer("🔐 Parolni kiriting:")

@dp.callback_query_handler(lambda c: c.data == "forgot_password")
async def process_forgot_password(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    admin_phone = get_setting("admin_phone")
    if admin_phone:
        masked = "*" * (len(admin_phone) - 4) + admin_phone[-4:]
        await callback.message.answer(
            f"📞 Sizning telefoningiz: {masked}\n"
            "Agar bu raqam sizniki bo‘lsa, to‘liq raqamni kiriting:"
        )
    else:
        await callback.message.answer("📞 Telefon raqamingizni xalqaro formatda yozing:")
    reset_sessions[user_id] = {'step': 'waiting_phone'}
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data == "continue_reset")
async def continue_reset(callback: types.CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    session = reset_sessions.get(user_id, {})
    step = session.get('step')
    if step == 'waiting_phone':
        await callback.message.answer("📞 Telefon raqamingizni kiriting:")
    elif step == 'waiting_code':
        await callback.message.answer("🔢 Kodni kiriting:")
    elif step == 'waiting_new_password':
        await callback.message.answer("🔐 Yangi parolni kiriting (kamida 4 belgi):")
    else:
        del reset_sessions[user_id]
        await callback.message.answer("Bekor qilindi. /start ni bosing.")

@dp.callback_query_handler(lambda c: c.data == "cancel_reset")
async def cancel_reset(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id in reset_sessions:
        del reset_sessions[user_id]
    await callback.answer("Bekor qilindi.")
    await callback.message.answer("Bosh sahifa. /start ni bosing.")

@dp.callback_query_handler(lambda c: c.data == "back_to_main")
async def back_to_main(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.answer("Asosiy menyu:", reply_markup=main_menu(callback.from_user.id))
    await callback.message.delete()

# -------------------- PAROLNI TEKSHIRISH (LOGIN) --------------------
@dp.message_handler(lambda message: message.text and message.from_user.id not in authenticated_users and message.from_user.id not in reset_sessions and message.from_user.id not in change_phone_sessions and message.from_user.id not in change_password_sessions and message.from_user.id not in registration_sessions)
async def handle_password_input(message: types.Message):
    user_id = message.from_user.id
    logger.info(f"Parol tekshirilmoqda: user {user_id}")
    password_hash = get_setting("password_hash")
    if password_hash and db_check_password(message.text, password_hash):
        add_user(
            user_id=user_id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name or ""
        )
        user = get_user(user_id)
        if user and user[6] and user[7]:
            authenticated_users.add(user_id)
            await message.answer("✅ Parol to‘g‘ri. Xush kelibsiz!", reply_markup=main_menu(user_id))
        else:
            registration_sessions[user_id] = {'step': 'waiting_phone'}
            await message.answer("📱 Iltimos, telefon raqamingizni kiriting (masalan: +998901234567):")
    else:
        logger.info(f"User {user_id} noto‘g‘ri parol kiritdi")
        await message.answer("❌ Parol noto‘g‘ri. Qayta urinib ko‘ring yoki 'Parolni unutdingizmi?' tugmasini bosing.")

# -------------------- RO'YXATDAN O'TISH JARAYONI --------------------
@dp.message_handler(lambda message: message.from_user.id in registration_sessions)
async def handle_registration(message: types.Message):
    user_id = message.from_user.id
    session = registration_sessions[user_id]
    step = session.get('step')

    if step == 'waiting_phone':
        phone = message.text.strip()
        if not (phone.startswith('+') and phone[1:].isdigit() or phone.isdigit()):
            await message.answer("❌ Telefon raqam noto‘g‘ri formatda. Iltimos, +998901234567 shaklida yozing.")
            return
        if not phone.startswith('+'):
            phone = '+' + phone
        session['phone'] = phone
        session['step'] = 'waiting_name'
        await message.answer("👤 Endi ism-familiyangizni kiriting (masalan: Adham Zokirov):")

    elif step == 'waiting_name':
        full_name = message.text.strip()
        if len(full_name) < 2:
            await message.answer("❌ Ism juda qisqa. Qayta kiriting:")
            return
        phone = session.get('phone')
        update_user_phone_name(user_id, phone, full_name)
        authenticated_users.add(user_id)
        del registration_sessions[user_id]
        await message.answer("✅ Ma'lumotlaringiz saqlandi. Endi botdan to‘liq foydalanishingiz mumkin.", reply_markup=main_menu(user_id))

# -------------------- RESET JARAYONI (parolni tiklash) --------------------
@dp.message_handler(lambda message: message.from_user.id in reset_sessions)
async def handle_reset(message: types.Message):
    user_id = message.from_user.id
    session = reset_sessions[user_id]
    step = session.get('step')

    if step == 'setup_phone':
        phone = message.text.strip()
        if not phone.startswith('+') or not phone[1:].isdigit():
            await message.answer("❌ Telefon raqam noto‘g‘ri formatda. Iltimos, +998901234567 shaklida yozing.")
            return
        session['phone'] = phone
        session['step'] = 'setup_password'
        await message.answer("Endi bot uchun parol o'rnating (kamida 4 belgi):")

    elif step == 'setup_password':
        password = message.text.strip()
        if len(password) < 4:
            await message.answer("❌ Parol juda qisqa. Kamida 4 belgidan iborat bo‘lsin.")
            return
        hashed = hash_password(password)
        set_setting("password_hash", hashed)
        set_setting("admin_phone", session['phone'])
        add_user(
            user_id=user_id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name or ""
        )
        authenticated_users.add(user_id)
        del reset_sessions[user_id]
        await message.answer("✅ Bot sozlandi! Endi to‘liq foydalanishingiz mumkin.", reply_markup=main_menu(user_id))

    elif step == 'waiting_phone':
        phone = message.text.strip()
        admin_phone = get_setting("admin_phone")
        if phone != admin_phone:
            await message.answer("❌ Bu telefon raqam tizimda mavjud emas. Qayta urinib ko‘ring.")
            return
        code = str(random.randint(100000, 999999))
        session['code'] = code
        session['step'] = 'waiting_code'
        session['phone'] = phone
        send_sms_code(phone, code)
        await message.answer("✅ Sizning telefon raqamingizga 6 xonali kod yuborildi. Kodni kiriting:")

    elif step == 'waiting_code':
        user_code = message.text.strip()
        if user_code != session.get('code'):
            await message.answer("❌ Kod noto‘g‘ri. Qayta urinib ko‘ring.")
            return
        session['step'] = 'waiting_new_password'
        await message.answer("✅ Kod tasdiqlandi. Endi yangi parolni kiriting:")

    elif step == 'waiting_new_password':
        new_pass = message.text.strip()
        if len(new_pass) < 4:
            await message.answer("❌ Parol juda qisqa. Kamida 4 belgidan iborat bo‘lsin.")
            return
        hashed = hash_password(new_pass)
        set_setting("password_hash", hashed)
        authenticated_users.add(user_id)
        del reset_sessions[user_id]
        await message.answer("✅ Parol muvaffaqiyatli o‘zgartirildi. Endi tizimga kirdingiz.", reply_markup=main_menu(user_id))

# -------------------- ADMIN TUGMASI (hamma ko‘radi) --------------------
@dp.message_handler(lambda msg: msg.text == "👤 Admin")
@authenticated_only
async def handle_admin_button(message: types.Message):
    """Admin bilan bog'lanish haqida chiroyli ma'lumot"""
    from bot.config import ADMIN_USERNAME
    
    if ADMIN_USERNAME:
        # Chiroyli formatlangan xabar
        text = (
            "📢 **Admin bilan bog‘lanish**\n\n"
            f"👤 **Username:** @{ADMIN_USERNAME}\n"
            "💬 **Murojaat uchun:** Yuqoridagi username orqali yozishingiz mumkin.\n\n"
            "📌 *Eslatma: Admin faqat muhim masalalar bo‘yicha javob beradi.*"
        )
        # Inline tugma qo'shamiz (ixtiyoriy)
        keyboard = InlineKeyboardMarkup().add(
            InlineKeyboardButton("📩 Admin ga yozish", url=f"https://t.me/{ADMIN_USERNAME}")
        )
        await message.answer(text, parse_mode="Markdown", reply_markup=keyboard)
    else:
        await message.answer(
            "❌ **Admin maʼlumoti mavjud emas.**\n"
            "Iltimos, keyinroq urinib koʻring yoki administrator bilan bogʻlaning.",
            parse_mode="Markdown"
        )
# -------------------- SOZLAMALAR MENYUSI --------------------
@dp.message_handler(lambda msg: msg.text == "⚙️ Sozlamalar")
@authenticated_only
async def handle_settings_button(message: types.Message):
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton("📱 Telefon raqamni o'zgartirish", callback_data="change_phone"),
        InlineKeyboardButton("🔐 Parolni o'zgartirish", callback_data="change_password"),
        InlineKeyboardButton("🔙 Ortga", callback_data="back_to_main")
    )
    await message.answer("⚙️ Sozlamalar:", reply_markup=keyboard)

@dp.callback_query_handler(lambda c: c.data == "change_phone")
async def change_phone_start(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    change_phone_sessions[user_id] = {'step': 'waiting_new_phone'}
    await callback.answer()
    await callback.message.answer(
        "📱 Yangi telefon raqamingizni xalqaro formatda yozing (masalan: +998901234567):"
    )

@dp.callback_query_handler(lambda c: c.data == "change_password")
async def change_password_start(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    change_password_sessions[user_id] = {'step': 'waiting_old_password'}
    await callback.answer()
    await callback.message.answer("🔐 Eski parolni kiriting:")

# -------------------- TELEFON RAQAMNI O‘ZGARTIRISH --------------------
@dp.message_handler(lambda message: message.from_user.id in change_phone_sessions)
@authenticated_only
async def handle_change_phone(message: types.Message):
    user_id = message.from_user.id
    session = change_phone_sessions[user_id]
    step = session.get('step')

    if step == 'waiting_new_phone':
        new_phone = message.text.strip()
        if not new_phone.startswith('+') or not new_phone[1:].isdigit():
            await message.answer("❌ Telefon raqam noto‘g‘ri formatda. Iltimos, +998901234567 shaklida yozing.")
            return
        code = str(random.randint(100000, 999999))
        session['new_phone'] = new_phone
        session['code'] = code
        session['step'] = 'waiting_code'
        send_sms_code(new_phone, code)
        await message.answer("✅ Yangi raqamingizga 6 xonali kod yuborildi. Kodni kiriting:")

    elif step == 'waiting_code':
        user_code = message.text.strip()
        if user_code != session.get('code'):
            await message.answer("❌ Kod noto‘g‘ri. Qayta urinib ko‘ring.")
            return
        set_setting("admin_phone", session['new_phone'])
        del change_phone_sessions[user_id]
        await message.answer("✅ Telefon raqam muvaffaqiyatli o‘zgartirildi.", reply_markup=main_menu(user_id))

# -------------------- PAROLNI O‘ZGARTIRISH --------------------
@dp.message_handler(lambda message: message.from_user.id in change_password_sessions)
@authenticated_only
async def handle_change_password(message: types.Message):
    user_id = message.from_user.id
    session = change_password_sessions[user_id]
    step = session.get('step')
    password_hash = get_setting("password_hash")

    if step == 'waiting_old_password':
        old_pass = message.text.strip()
        if db_check_password(old_pass, password_hash):
            session['step'] = 'waiting_new_password'
            await message.answer("✅ Eski parol to‘g‘ri. Endi yangi parolni kiriting (kamida 4 belgi):")
        else:
            await message.answer("❌ Eski parol noto‘g‘ri. Qayta urinib ko‘ring.")

    elif step == 'waiting_new_password':
        new_pass = message.text.strip()
        if len(new_pass) < 4:
            await message.answer("❌ Parol juda qisqa. Kamida 4 belgidan iborat bo‘lsin.")
            return
        new_hashed = hash_password(new_pass)
        set_setting("password_hash", new_hashed)
        del change_password_sessions[user_id]
        await message.answer("✅ Parol muvaffaqiyatli o‘zgartirildi!", reply_markup=main_menu(user_id))

# -------------------- FOYDALANUVCHILAR TUGMASI (faqat admin) --------------------
@dp.message_handler(lambda msg: msg.text == "👥 Foydalanuvchilar")
@authenticated_only
async def handle_users_button(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Bu tugma faqat admin uchun.")
        return
    await list_users(message)

# -------------------- ADMIN BUYRUGLARI (inline tugmalar bilan) --------------------
async def list_users(message: types.Message):
    users = get_all_users()
    if not users:
        await message.answer("📭 Hozircha foydalanuvchilar yo'q.")
        return

    admin_id = ADMIN_ID
    for user in users:
        user_id, username, first_name, last_name, is_banned, joined_at, phone, full_name = user
        if user_id == admin_id:
            continue
        status = "🚫 Bloklangan" if is_banned else "✅ Faol"
        name_display = full_name or f"{first_name} {last_name or ''}".strip()
        username_display = f"@{username}" if username else "no username"
        phone_display = phone or "—"

        text = (f"ID: {user_id}\n"
                f"Ism: {name_display}\n"
                f"Telefon: {phone_display}\n"
                f"Username: {username_display}\n"
                f"Status: {status}\n"
                f"Kirdi: {joined_at}")

        keyboard = InlineKeyboardMarkup(row_width=2)
        if is_banned:
            keyboard.add(InlineKeyboardButton("🔓 Blokdan chiqarish", callback_data=f"unban_{user_id}"))
        else:
            keyboard.add(InlineKeyboardButton("🔒 Bloklash", callback_data=f"ban_{user_id}"))
        keyboard.add(InlineKeyboardButton("❌ O'chirish", callback_data=f"delete_{user_id}"))

        await message.answer(text, reply_markup=keyboard)

@dp.message_handler(commands=['users'])
@authenticated_only
async def users_command(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Bu buyruq faqat admin uchun.")
        return
    await list_users(message)

@dp.callback_query_handler(lambda c: c.data.startswith('ban_') and c.data.split('_')[1].isdigit())
async def ban_user_callback(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Faqat admin uchun!", show_alert=True)
        return
    target_id = int(callback.data.split('_')[1])
    ban_user(target_id)
    if target_id in authenticated_users:
        authenticated_users.remove(target_id)
    await callback.answer(f"✅ Foydalanuvchi {target_id} bloklandi")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.edit_text(callback.message.text + "\n\n🚫 Bloklangan")

@dp.callback_query_handler(lambda c: c.data.startswith('unban_') and c.data.split('_')[1].isdigit())
async def unban_user_callback(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Faqat admin uchun!", show_alert=True)
        return
    target_id = int(callback.data.split('_')[1])
    unban_user(target_id)
    await callback.answer(f"✅ Foydalanuvchi {target_id} blokdan chiqarildi")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.edit_text(callback.message.text + "\n\n✅ Blokdan chiqarilgan")

@dp.callback_query_handler(lambda c: c.data.startswith('delete_') and c.data.split('_')[1].isdigit())
async def delete_user_callback(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Faqat admin uchun!", show_alert=True)
        return
    target_id = int(callback.data.split('_')[1])
    delete_user(target_id)
    if target_id in authenticated_users:
        authenticated_users.remove(target_id)
    await callback.answer(f"✅ Foydalanuvchi {target_id} o'chirildi")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.edit_text(callback.message.text + "\n\n❌ O'chirilgan")

# Eski /ban va /unban buyruqlari
@dp.message_handler(commands=['ban'])
@authenticated_only
async def ban_user_cmd(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Bu buyruq faqat admin uchun.")
        return
    args = message.get_args().split()
    if not args:
        await message.answer("❌ Foydalanish: /ban user_id")
        return
    try:
        target_id = int(args[0])
        ban_user(target_id)
        if target_id in authenticated_users:
            authenticated_users.remove(target_id)
        await message.answer(f"✅ Foydalanuvchi {target_id} bloklandi.")
    except ValueError:
        await message.answer("❌ user_id son bo‘lishi kerak.")

@dp.message_handler(commands=['unban'])
@authenticated_only
async def unban_user_cmd(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Bu buyruq faqat admin uchun.")
        return
    args = message.get_args().split()
    if not args:
        await message.answer("❌ Foydalanish: /unban user_id")
        return
    try:
        target_id = int(args[0])
        unban_user(target_id)
        await message.answer(f"✅ Foydalanuvchi {target_id} blokdan chiqarildi.")
    except ValueError:
        await message.answer("❌ user_id son bo‘lishi kerak.")

# -------------------- O'CHIRISH CALLBACKLARI (client/order) --------------------
@dp.callback_query_handler(lambda c: c.data == "delete_choose_client")
async def process_delete_client_choice(callback: types.CallbackQuery):
    if callback.from_user.id not in authenticated_users:
        await callback.answer("Avval tizimga kiring.", show_alert=True)
        return
    await callback.answer()
    await show_clients_for_delete(callback.message)

@dp.callback_query_handler(lambda c: c.data == "delete_choose_order")
async def process_delete_order_choice(callback: types.CallbackQuery):
    if callback.from_user.id not in authenticated_users:
        await callback.answer("Avval tizimga kiring.", show_alert=True)
        return
    await callback.answer()
    await show_orders_for_delete(callback.message)

@dp.callback_query_handler(lambda c: c.data.startswith("del_client:"))
async def process_delete_client(callback: types.CallbackQuery):
    if callback.from_user.id not in authenticated_users:
        await callback.answer("Avval tizimga kiring.", show_alert=True)
        return
    await delete_client_callback(callback)

@dp.callback_query_handler(lambda c: c.data.startswith("del_order:"))
async def process_delete_order(callback: types.CallbackQuery):
    if callback.from_user.id not in authenticated_users:
        await callback.answer("Avval tizimga kiring.", show_alert=True)
        return
    await delete_order_callback(callback)

# -------------------- REPLY TUGMALAR --------------------
@dp.message_handler(lambda msg: msg.text == "➕ Klient qo'shish")
@authenticated_only
async def handle_add_client_button(message: types.Message):
    await add_client_cmd(message)

@dp.message_handler(lambda msg: msg.text == "📋 Klientlar ro'yxati")
@authenticated_only
async def handle_list_clients_button(message: types.Message):
    await list_clients_handler(message)

@dp.message_handler(lambda msg: msg.text == "🛍 Buyurtma qo'shish")
@authenticated_only
async def handle_add_order_button(message: types.Message):
    await add_order_cmd(message)

@dp.message_handler(lambda msg: msg.text == "📊 Excel export")
@authenticated_only
async def handle_export_button(message: types.Message):
    await export_orders_excel(message)

@dp.message_handler(lambda msg: msg.text == "🗑 O'chirish")
@authenticated_only
async def handle_delete_button(message: types.Message):
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("👤 Klient o'chirish", callback_data="delete_choose_client"),
        InlineKeyboardButton("📦 Buyurtma o'chirish", callback_data="delete_choose_order")
    )
    await message.answer("Nimani o'chirmoqchisiz?", reply_markup=keyboard)

# -------------------- UNIVERSAL HANDLER --------------------
@dp.message_handler(lambda message: "," in message.text)
@authenticated_only
async def universal_input(message: types.Message):
    parts = [p.strip() for p in message.text.split(",")]

    if len(parts) == 2:
        name, phone = parts
        phone = phone.replace(" ", "")
        if not (phone.startswith('+') and phone[1:].isdigit() or phone.isdigit()):
            await message.answer(
                "❌ Telefon raqam noto‘g‘ri formatda.\n"
                "Klient qo‘shish uchun: `Ism, Telefon` yoki `Ism, Telefon, Manzil`\n"
                "Misol: `Adham, +998901234567`",
                reply_markup=main_menu(message.from_user.id)
            )
            return
        if not phone.startswith('+'):
            phone = '+' + phone
        address = ""
        add_client(name, phone, address)
        await message.answer(f"✅ Klient qo‘shildi: {name}", reply_markup=main_menu(message.from_user.id))

    elif len(parts) == 3:
        first, second, third = parts
        if first.isdigit():
            client_index = int(first)
            product = second
            amount_str = third
            amount_digits = re.sub(r'\D', '', amount_str)
            if not amount_digits:
                await message.answer(
                    "❌ Xato: Miqdor raqam bo‘lishi kerak.\n"
                    "Masalan: `1, Anor, 5` yoki `1, Anor, 3kg`",
                    reply_markup=main_menu(message.from_user.id)
                )
                return
            amount = int(amount_digits)
            clients = get_clients()
            if 1 <= client_index <= len(clients):
                client_id = clients[client_index-1][0]
                add_order(client_id, product, amount)
                await message.answer(f"✅ Buyurtma qo‘shildi: {product} ({amount})", reply_markup=main_menu(message.from_user.id))
            else:
                await message.answer("❌ Bunday raqamli klient mavjud emas.", reply_markup=main_menu(message.from_user.id))
        else:
            name, phone, address = first, second, third
            phone = phone.replace(" ", "")
            if not (phone.startswith('+') and phone[1:].isdigit() or phone.isdigit()):
                await message.answer(
                    "❌ Telefon raqam noto‘g‘ri formatda.\n"
                    "Klient qo‘shish uchun: `Ism, Telefon, Manzil`\n"
                    "Misol: `Adham, +998901234567, Samarqand`",
                    reply_markup=main_menu(message.from_user.id)
                )
                return
            if not phone.startswith('+'):
                phone = '+' + phone
            add_client(name, phone, address)
            await message.answer(f"✅ Klient qo‘shildi: {name}", reply_markup=main_menu(message.from_user.id))
    else:
        await message.answer(
            "❌ Noto‘g‘ri format. Iltimos:\n"
            "• Klient qo‘shish: `Ism, Telefon` yoki `Ism, Telefon, Manzil`\n"
            "• Buyurtma qo‘shish: `Klient raqami, Mahsulot, Miqdor`\n"
            "Misol: `Adham, +998901234567, Samarqand` yoki `1, Anor, 3kg`",
            reply_markup=main_menu(message.from_user.id)
        )

# -------------------- MATNLI KOMANDALAR --------------------
@dp.message_handler(commands=['add_client'])
@authenticated_only
async def add_client_command(message: types.Message):
    await add_client_cmd(message)

@dp.message_handler(commands=['clients'])
@authenticated_only
async def clients_command(message: types.Message):
    await list_clients_handler(message)

@dp.message_handler(commands=['add_order'])
@authenticated_only
async def add_order_command(message: types.Message):
    await add_order_cmd(message)

@dp.message_handler(commands=['export'])
@authenticated_only
async def export_command(message: types.Message):
    await export_orders_excel(message)

if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
