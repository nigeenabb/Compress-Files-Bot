import os
import io
import logging
import tempfile
import subprocess
import base64
import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from PIL import Image

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN     = os.getenv("BOT_TOKEN", "YOUR_TELEGRAM_TOKEN")
GROQ_KEY = os.getenv("GROQ_API_KEY", "YOUR_GROQ_KEY")
user_settings = {}
user_history  = {}
pending_files = {}  # fayl kelganda vaqtincha saqlanadi

def get_settings(uid):
    return user_settings.get(uid, {"format": "auto", "quality": 85, "ai_mode": True})

def fmt_size(b):
    if b < 1024: return f"{b} B"
    elif b < 1024**2: return f"{b/1024:.1f} KB"
    else: return f"{b/1024**2:.2f} MB"

async def ask_claude(messages: list) -> str:
    system_text = (
        "Sen Telegram fayllar siquvchi botning AI yordamchisisisan. "
        "Foydalanuvchilarga rasm, PDF, Word, Excel, video fayllarni siqish, "
        "optimallashtirish va tahlil qilishda yordam berasan. "
        "Javoblarni qisqa va aniq ber. Uzbek tilida javob ber."
    )
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROQ_KEY}",
                "content-type": "application/json",
            },
            json={
                "model": "llama-3.3-70b-versatile",
                "max_tokens": 1024,
                "messages": [{"role": "system", "content": system_text}] + messages,
            }
        )
        data = resp.json()
        logger.info(f"Groq response: {data}")
        if "choices" not in data:
            error_msg = data.get("error", {}).get("message", str(data))
            raise Exception(f"API xato: {error_msg}")
        return data["choices"][0]["message"]["content"]

async def analyze_image_claude(image_bytes: bytes, prompt: str) -> str:

    b64 = base64.standard_b64encode(image_bytes).decode()
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROQ_KEY}",
                "content-type": "application/json",
            },
            json={
                "model": "meta-llama/llama-4-scout-17b-16e-instruct",
                "max_tokens": 1024,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                        {"type": "text", "text": prompt}
                    ]
                }]
            }
        )
        data = resp.json()
        logger.info(f"Groq vision response: {data}")
        if "choices" not in data:
            error_msg = data.get("error", {}).get("message", str(data))
            raise Exception(f"API xato: {error_msg}")
        return data["choices"][0]["message"]["content"]

# /start

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE): # Pastda doim ko'rinadi
    reply_kb = ReplyKeyboardMarkup([
        [KeyboardButton("📦 Fayl yuborish"), KeyboardButton("🤖 AI chat")],
        [KeyboardButton("⚙️ Sozlamalar"),   KeyboardButton("❓ Yordam")],
    ], resize_keyboard=True, input_field_placeholder="Fayl yuboring yoki savol yozing...")

    inline_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 Qanday fayl yuborish?", callback_data="help_file"),
         InlineKeyboardButton("🤖 AI nima qila oladi?",  callback_data="help_ai")],
        [InlineKeyboardButton("⚙️ Sozlamalar",           callback_data="back"),
         InlineKeyboardButton("📊 Statistika",           callback_data="stats")],
    ])

    text = (
        "👋 *Salom! Men — Compress Files Bot*\n\n"
        "Fayllaringiz hajmini kamaytiradigan aqlli bot!\n\n"
        "✅ Rasm — JPEG, PNG, WebP\n"
        "✅ PDF, Word, Excel, PowerPoint\n"
        "✅ Video\n"
        "🤖 AI bilan suhbat va tahlil\n\n"
        "👇 Quyidagi tugmalardan foydalaning:"
    )
    await update.message.reply_text(text, parse_mode="Markdown",
                                    reply_markup=reply_kb)
    await update.message.reply_text("🚀 *Nimadan boshlaysiz?*",
                                    parse_mode="Markdown",
                                    reply_markup=inline_kb)

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 *Foydalanish:*\n\n"
        "*Fayl siqish:*\n"
        "Shunchaki fayl yubor — bot siqib qaytaradi\n\n"
        "*AI tahlil:*\n"
        "Rasm + caption yoz (masalan: 'tahlil qil')\n\n"
        "*Chatbot:*\n"
        "Matn yoz — AI javob beradi\n\n"
        "*Qollab-quvvatlanadi:*\n"
        "JPEG, PNG, WebP, PDF, DOCX, XLSX, MP4"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def clear_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_history[uid] = []
    await update.message.reply_text("🗑 Chat tarixi tozalandi!")

# /settings

async def settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    s = get_settings(uid)
    ai_status = "✅ Yoqilgan" if s.get("ai_mode", True) else "❌ Ochirilgan"
    keyboard = [
        [InlineKeyboardButton("🖼 Format", callback_data="menu_format"),
         InlineKeyboardButton("⭐ Sifat", callback_data="menu_quality")],
        [InlineKeyboardButton(f"🤖 AI: {ai_status}", callback_data="toggle_ai")],
        [InlineKeyboardButton("✅ Yopish", callback_data="close")],
    ]
    fmt = s.get("format", "auto").upper()
    q = s.get("quality", 85)
    text = f"⚙️ *Sozlamalar:*\n\n📐 Format: `{fmt}`\n⭐ Sifat: `{q}`\n🤖 AI: {ai_status}"
    await update.message.reply_text(text, parse_mode="Markdown",
                                    reply_markup=InlineKeyboardMarkup(keyboard))

async def callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    data = q.data
    if uid not in user_settings:
        user_settings[uid] = get_settings(uid)

    if data == "close":
        await q.message.delete(); return

    if data == "help_file":
        await q.edit_message_text(
            "📦 *Fayl yuborish:*\n\n"
            "Shunchaki faylni chat ga tashlang!\n\n"
            "✅ Rasm — JPEG, PNG, WebP\n"
            "✅ PDF\n"
            "✅ Word (.docx)\n"
            "✅ Excel (.xlsx)\n"
            "✅ PowerPoint (.pptx)\n"
            "✅ Video — MP4, AVI, MOV\n\n"
            "Bot avtomatik aniqlab siqib qaytaradi!",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⬅️ Orqaga", callback_data="start_back")
            ]])
        ); return

    if data == "help_ai":
        await q.edit_message_text(
            "🤖 *AI nima qila oladi?*\n\n"
            "💬 Istalgan savolingga javob beradi\n"
            "🖼 Rasm yuborsan — tahlil qiladi\n"
            "💡 Fayl siqilgandan keyin maslahat beradi\n"
            "🧠 Chat tarixini eslab qoladi (20 xabar)\n"
            "🌍 Har qanday tilda gaplashadi\n\n"
            "⚙️ /settings da AI ni o\'chir/yoqish mumkin",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⬅️ Orqaga", callback_data="start_back")
            ]])
        ); return

    if data == "stats":
        total_users = len(user_settings)
        total_chats = sum(len(v) for v in user_history.values())
        await q.edit_message_text(
            f"📊 *Bot statistikasi:*\n\n"
            f"👥 Foydalanuvchilar: `{total_users}`\n"
            f"💬 Jami xabarlar: `{total_chats}`\n\n"
            f"🟢 Bot ishlayapti!",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⬅️ Orqaga", callback_data="start_back")
            ]])
        ); return

    # Pending file callbacks
    if data == "pf_cancel":
        pending_files.pop(uid, None)
        await q.edit_message_text("❌ Bekor qilindi."); return

    if data.startswith("pf_fmt_"):
        pf = pending_files.get(uid)
        if not pf:
            await q.edit_message_text("⚠️ Fayl topilmadi, qayta yuboring."); return
        fmt = data[7:]  # webp, jpeg, png, auto
        await q.edit_message_text("⏳ Siqilmoqda...")
        s = get_settings(uid)
        s["format"] = fmt
        try:
            result, out_ext, unchanged = compress_image(io.BytesIO(pf["data"]), s)
            new = len(result.getvalue())
            if unchanged:
                await ctx.bot.send_message(q.message.chat_id,
                    "⚠️ Bu rasm allaqachon optimal siqilgan!\n"
                    "Boshqa format tanlasangiz yaxshiroq natija berishi mumkin.")
                try: await q.message.delete()
                except: pass
            else:
                await send_result_msg(ctx, q.message.chat_id, result,
                                      f"compressed.{out_ext}", pf["orig"], new, q.message)
            if s.get("ai_mode", True):
                saved_pct = round((pf["orig"] - new) / pf["orig"] * 100, 1)
                advice = await ask_claude([{"role": "user", "content":
                    "Rasm " + fmt_size(pf['orig']) + " dan " + fmt_size(new) + f" ga siqildi ({saved_pct}% kamaydi). Format: {fmt.upper()}. Qisqa maslahat ber uzbek tilida."}])
                await ctx.bot.send_message(q.message.chat_id, f"💡 {advice}")
        except Exception as e:
            await ctx.bot.send_message(q.message.chat_id, f"❌ Xato: {e}")
        finally:
            pending_files.pop(uid, None)
        return

    if data.startswith("pf_crf_"):
        pf = pending_files.get(uid)
        if not pf:
            await q.edit_message_text("⚠️ Fayl topilmadi, qayta yuboring."); return
        crf = data[7:]  # 22, 26, 28, 32
        crf_labels = {"22": "Yuqori sifat", "26": "Optimal", "28": "O\'rtacha", "32": "Kichik hajm"}
        await q.edit_message_text(f"⏳ Video siqilmoqda... ({crf_labels.get(crf, '')})\nBiroz vaqt ketadi ⏱")
        try:
            result = compress_video_crf(pf["data"], pf["ext"], crf)
            new = len(result.getvalue())
            await send_result_msg(ctx, q.message.chat_id, result,
                                  "compressed.mp4", pf["orig"], new, q.message)
        except Exception as e:
            await ctx.bot.send_message(q.message.chat_id, f"❌ Xato: {e}")
        finally:
            pending_files.pop(uid, None)
        return

    if data == "start_back":
        inline_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📦 Qanday fayl yuborish?", callback_data="help_file"),
             InlineKeyboardButton("🤖 AI nima qila oladi?",  callback_data="help_ai")],
            [InlineKeyboardButton("⚙️ Sozlamalar",           callback_data="back"),
             InlineKeyboardButton("📊 Statistika",           callback_data="stats")],
        ])
        await q.edit_message_text("🚀 *Nimadan boshlaysiz?*",
                                  parse_mode="Markdown",
                                  reply_markup=inline_kb); return

    if data == "toggle_ai":
        user_settings[uid]["ai_mode"] = not user_settings[uid].get("ai_mode", True)
        st = "Yoqildi" if user_settings[uid]["ai_mode"] else "Ochirildi"
        await q.edit_message_text(f"🤖 AI rejimi: {st}"); return

    if data == "menu_format":
        kb = [
            [InlineKeyboardButton("🤖 Auto", callback_data="fmt_auto"),
             InlineKeyboardButton("🌐 WebP", callback_data="fmt_webp")],
            [InlineKeyboardButton("📷 JPEG", callback_data="fmt_jpeg"),
             InlineKeyboardButton("🖼 PNG",  callback_data="fmt_png")],
            [InlineKeyboardButton("⬅️ Orqaga", callback_data="back")],
        ]
        await q.edit_message_text("📐 *Format tanlang:*", parse_mode="Markdown",
                                  reply_markup=InlineKeyboardMarkup(kb)); return

    if data == "menu_quality":
        kb = [
            [InlineKeyboardButton("🔴 60", callback_data="q_60"),
             InlineKeyboardButton("🟡 75", callback_data="q_75")],
            [InlineKeyboardButton("🟢 85 (tavsiya)", callback_data="q_85"),
             InlineKeyboardButton("⚪ 95", callback_data="q_95")],
            [InlineKeyboardButton("⬅️ Orqaga", callback_data="back")],
        ]
        await q.edit_message_text("⭐ *Sifat tanlang:*", parse_mode="Markdown",
                                  reply_markup=InlineKeyboardMarkup(kb)); return

    if data.startswith("fmt_"):
        user_settings[uid]["format"] = data[4:]
        await q.edit_message_text(f"✅ Format `{data[4:].upper()}` ga ozgartirildi!", parse_mode="Markdown"); return

    if data.startswith("q_"):
        user_settings[uid]["quality"] = int(data[2:])
        await q.edit_message_text(f"✅ Sifat `{data[2:]}` ga ozgartirildi!", parse_mode="Markdown"); return

    if data == "back":
        s = get_settings(uid)
        ai_status = "✅ Yoqilgan" if s.get("ai_mode", True) else "❌ Ochirilgan"
        kb = [
            [InlineKeyboardButton("🖼 Format", callback_data="menu_format"),
             InlineKeyboardButton("⭐ Sifat", callback_data="menu_quality")],
            [InlineKeyboardButton(f"🤖 AI: {ai_status}", callback_data="toggle_ai")],
            [InlineKeyboardButton("✅ Yopish", callback_data="close")]
        ]
        fmt = s.get("format", "auto").upper()
        qv  = s.get("quality", 85)
        await q.edit_message_text(
            f"⚙️ *Sozlamalar:*\n\n📐 Format: `{fmt}`\n⭐ Sifat: `{qv}`\n🤖 AI: {ai_status}",
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

# ─── Matn handler (chatbot)

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()

    # Reply keyboard tugmalari
    if text == "❓ Yordam":
        await help_cmd(update, ctx); return
    if text == "⚙️ Sozlamalar":
        await settings(update, ctx); return
    if text == "📦 Fayl yuborish":
        await update.message.reply_text(
            "📦 *Fayl yuborish:*\n\n"
            "Shunchaki faylni chat ga tashlang!\n\n"
            "Qabul qilinadi:\n"
            "🖼 Rasm — JPEG, PNG, WebP\n"
            "📄 PDF\n"
            "📝 Word (.docx)\n"
            "📊 Excel (.xlsx)\n"
            "📑 PowerPoint (.pptx)\n"
            "🎥 Video — MP4, AVI, MOV",
            parse_mode="Markdown"); return
    if text == "🤖 AI chat":
        await update.message.reply_text(
            "🤖 *AI chat yoqildi!*\n\n"
            "Endi istalgan savolingni yoz — javob beraman.\n"
            "Rasm yuborsan — tahlil ham qilaman!\n\n"
            "/clear — chat tarixini tozalash",
            parse_mode="Markdown"); return

    # AI chat
    if uid not in user_history:
        user_history[uid] = []
    user_history[uid].append({"role": "user", "content": text})
    if len(user_history[uid]) > 20:
        user_history[uid] = user_history[uid][-20:]
    msg = await update.message.reply_text("🤖 Javob yozilmoqda...")
    try:
        reply = await ask_claude(user_history[uid])
        user_history[uid].append({"role": "assistant", "content": reply})
        await msg.edit_text(reply)
    except Exception as e:
        await msg.edit_text(f"❌ AI xato: {e}")

# ─── Rasm handler

async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    s = get_settings(uid)
    caption = update.message.caption or ""
    photo = update.message.photo[-1]
    file = await ctx.bot.get_file(photo.file_id)
    data = bytes(await file.download_as_bytearray())
    orig = len(data)

    wants_analysis = any(w in caption.lower() for w in
        ["tahlil", "nima", "ayt", "tushun", "describe", "what", "analyze"])

    if wants_analysis and s.get("ai_mode", True):
        msg = await update.message.reply_text("🤖 AI tahlil qilmoqda...")
        prompt = caption if caption else "Bu rasmni uzbek tilida tahlil qil. Nimalar korinadi? Siqish uchun maslahat ber."
        try:
            analysis = await analyze_image_claude(data, prompt)
            await msg.edit_text(f"🤖 *AI tahlili:*\n\n{analysis}", parse_mode="Markdown")
        except Exception as e:
            await msg.edit_text(f"❌ AI xato: {e}")
        return

    # Fayl vaqtincha saqlab, format so'rash
    pending_files[uid] = {"data": data, "type": "image", "orig": orig, "ext": "jpg"}
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 WebP (eng kichik)", callback_data="pf_fmt_webp"),
         InlineKeyboardButton("📷 JPEG",              callback_data="pf_fmt_jpeg")],
        [InlineKeyboardButton("🖼 PNG",               callback_data="pf_fmt_png"),
         InlineKeyboardButton("⚡ Auto (tavsiya)",    callback_data="pf_fmt_auto")],
        [InlineKeyboardButton("❌ Bekor qilish",      callback_data="pf_cancel")],
    ])
    await update.message.reply_text(
        f"🖼 *Rasm qabul qilindi!*\n📦 Hajmi: `{fmt_size(orig)}`\n\n"
        "📐 *Qaysi formatda tayyorlayin?*",
        parse_mode="Markdown", reply_markup=keyboard)

# Document handler 
async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    s = get_settings(uid)
    doc = update.message.document
    mime = doc.mime_type or ""
    fname = doc.file_name or "file"
    ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
    caption = update.message.caption or ""
    msg = await update.message.reply_text("⏳ Yuklanmoqda...")
    file = await ctx.bot.get_file(doc.file_id)
    data = bytes(await file.download_as_bytearray())
    orig = len(data)

    try:
        if mime.startswith("image/") or ext in ("jpg","jpeg","png","webp","bmp"):
            await msg.edit_text("⏳ Rasm siqilmoqda...")
            result, out_ext = compress_image(io.BytesIO(data), s)
            new = len(result.getvalue())
            await send_result(update, ctx, result, f"compressed.{out_ext}", orig, new, msg)
            if s.get("ai_mode", True):
                prompt = caption if caption else "Bu rasmni qisqacha tahlil qil va siqish bo'yicha maslahat ber uzbek tilida."
                analysis = await analyze_image_claude(data, prompt)
                await update.message.reply_text(f"🤖 {analysis}")

        elif mime == "application/pdf" or ext == "pdf":
            await msg.edit_text("⏳ PDF siqilmoqda...")
            result = compress_pdf(data)
            new = len(result.getvalue())
            await send_result(update, ctx, result, "compressed.pdf", orig, new, msg)
            if s.get("ai_mode", True):
                advice = await ask_claude([{"role": "user", "content":
                    f"PDF fayl {fmt_size(orig)} dan {fmt_size(new)} ga siqildi. "
                    "Foydalanuvchiga qisqa maslahat ber uzbek tilida."}])
                await update.message.reply_text(f"💡 {advice}")

        elif ext == "docx" or "wordprocessingml" in mime:
            await msg.edit_text("⏳ Word fayl siqilmoqda...")
            result = compress_office(data, s.get("quality", 85))
            new = len(result.getvalue())
            await send_result(update, ctx, result, "compressed.docx", orig, new, msg)
            if s.get("ai_mode", True):
                advice = await ask_claude([{"role": "user", "content":
                    f"Word (.docx) fayl {fmt_size(orig)} dan {fmt_size(new)} ga siqildi. "
                    "Hajmni yanada kamaytirish uchun qisqa maslahat ber uzbek tilida."}])
                await update.message.reply_text(f"💡 {advice}")

        elif ext == "xlsx" or "spreadsheetml" in mime:
            await msg.edit_text("⏳ Excel fayl siqilmoqda...")
            result = compress_office(data, s.get("quality", 85))
            new = len(result.getvalue())
            await send_result(update, ctx, result, "compressed.xlsx", orig, new, msg)
            if s.get("ai_mode", True):
                advice = await ask_claude([{"role": "user", "content":
                    f"Excel (.xlsx) fayl {fmt_size(orig)} dan {fmt_size(new)} ga siqildi. "
                    "Maslahat ber uzbek tilida."}])
                await update.message.reply_text(f"💡 {advice}")

        elif ext == "pptx" or "presentationml" in mime:
            await msg.edit_text("⏳ PowerPoint siqilmoqda...")
            result = compress_office(data, s.get("quality", 85))
            new = len(result.getvalue())
            await send_result(update, ctx, result, "compressed.pptx", orig, new, msg)
            if s.get("ai_mode", True):
                advice = await ask_claude([{"role": "user", "content":
                    f"PowerPoint (.pptx) fayl {fmt_size(orig)} dan {fmt_size(new)} ga siqildi. "
                    "Hajmni yanada kamaytirish uchun qisqa maslahat ber uzbek tilida."}])
                await update.message.reply_text(f"💡 {advice}")

        elif mime.startswith("video/") or ext in ("mp4","mov","avi","mkv"):
            await msg.delete()
            pending_files[uid] = {"data": data, "type": "video", "orig": orig, "ext": ext or "mp4"}
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔴 Kichik hajm (past sifat)",   callback_data="pf_crf_32"),
                 InlineKeyboardButton("🟡 O\'rtacha",                  callback_data="pf_crf_28")],
                [InlineKeyboardButton("🟢 Yuqori sifat (katta hajm)",  callback_data="pf_crf_22"),
                 InlineKeyboardButton("⚡ Tavsiya (optimal)",           callback_data="pf_crf_26")],
                [InlineKeyboardButton("❌ Bekor qilish",                callback_data="pf_cancel")],
            ])
            await update.message.reply_text(
                f"🎥 *Video qabul qilindi!*\n📦 Hajmi: `{fmt_size(orig)}`\n\n"
                "📊 *Qaysi sifatda tayyorlayin?*",
                parse_mode="Markdown", reply_markup=keyboard)

        else:
            await msg.edit_text(
                f"⚠️ `{fname}` qollab-quvvatlanmaydi.\n"
                "Qabul qilinadi: JPEG, PNG, PDF, DOCX, XLSX, PPTX, MP4",
                parse_mode="Markdown")

    except Exception as e:
        logger.exception(e)
        await msg.edit_text(f"❌ Xato: {e}")

async def handle_video(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    msg = await update.message.reply_text("⏳ Yuklanmoqda...")
    file = await ctx.bot.get_file(update.message.video.file_id)
    data = bytes(await file.download_as_bytearray())
    orig = len(data)
    await msg.delete()
    pending_files[uid] = {"data": data, "type": "video", "orig": orig, "ext": "mp4"}
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔴 Kichik hajm",      callback_data="pf_crf_32"),
         InlineKeyboardButton("🟡 O\'rtacha",        callback_data="pf_crf_28")],
        [InlineKeyboardButton("🟢 Yuqori sifat",     callback_data="pf_crf_22"),
         InlineKeyboardButton("⚡ Tavsiya (optimal)", callback_data="pf_crf_26")],
        [InlineKeyboardButton("❌ Bekor qilish",      callback_data="pf_cancel")],
    ])
    await update.message.reply_text(
        f"🎥 *Video qabul qilindi!*\n📦 Hajmi: `{fmt_size(orig)}`\n\n"
        "📊 *Qaysi sifatda tayyorlayin?*",
        parse_mode="Markdown", reply_markup=keyboard)

# Siqish funksiyalari

def compress_image(src, s):
    img = Image.open(src)
    src.seek(0)
    original_bytes = src.read()
    orig_size = len(original_bytes)

    fmt = s.get("format", "auto")
    if fmt == "auto": fmt = "jpeg"
    save_fmt = "JPEG" if fmt == "jpg" else fmt.upper()
    if save_fmt == "JPEG" and img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    out = io.BytesIO()
    img.save(out, format=save_fmt, quality=s.get("quality", 85), optimize=True)
    new_size = len(out.getvalue())

    # Agar natija kattaroq bo'lsa — originalini qaytaramiz
    if new_size >= orig_size:
        orig_out = io.BytesIO(original_bytes)
        orig_out.seek(0)
        # original formatini aniqlaymiz
        orig_fmt = s.get("_orig_ext", "jpg")
        return orig_out, orig_fmt, True   # True = o'zgarmadi

    out.seek(0)
    return out, fmt.lower(), False

def compress_pdf(data):
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as ti:
        ti.write(data); ti_path = ti.name
    to_path = ti_path.replace(".pdf", "_out.pdf")
    subprocess.run([
        "gs", "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.4",
        "-dPDFSETTINGS=/ebook", "-dNOPAUSE", "-dQUIET", "-dBATCH",
        f"-sOutputFile={to_path}", ti_path
    ], check=True)
    with open(to_path, "rb") as f: out = io.BytesIO(f.read())
    os.unlink(ti_path); os.unlink(to_path)
    out.seek(0); return out

def compress_office(data, quality=85):
    import zipfile
    src = io.BytesIO(data)
    out = io.BytesIO()
    with zipfile.ZipFile(src, "r") as zin, \
         zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zout:
        for item in zin.infolist():
            raw = zin.read(item.filename)
            name_low = item.filename.lower()
            if any(name_low.endswith(e) for e in (".jpg",".jpeg",".png",".bmp")):
                try:
                    img = Image.open(io.BytesIO(raw))
                    buf = io.BytesIO()
                    if name_low.endswith(".png"):
                        img.save(buf, format="PNG", optimize=True)
                    else:
                        if img.mode != "RGB": img = img.convert("RGB")
                        img.save(buf, format="JPEG", quality=quality, optimize=True)
                    compressed = buf.getvalue()
                    raw = compressed if len(compressed) < len(raw) else raw
                except Exception: pass
            zout.writestr(item, raw)
    out.seek(0); return out

def compress_video(data, ext):
    with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as ti:
        ti.write(data); ti_path = ti.name
    to_path = ti_path.replace(f".{ext}", "_out.mp4")
    subprocess.run([
        "ffmpeg", "-i", ti_path, "-vcodec", "libx264", "-crf", "28",
        "-preset", "fast", "-acodec", "aac", "-b:a", "128k", "-y", to_path
    ], check=True, capture_output=True)
    with open(to_path, "rb") as f: out = io.BytesIO(f.read())
    os.unlink(ti_path); os.unlink(to_path)
    out.seek(0); return out

# Natija

def compress_video_crf(data, ext, crf="28"):
    with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as ti:
        ti.write(data); ti_path = ti.name
    to_path = ti_path.replace(f".{ext}", "_out.mp4")
    subprocess.run([
        "ffmpeg", "-i", ti_path, "-vcodec", "libx264", f"-crf", crf,
        "-preset", "fast", "-acodec", "aac", "-b:a", "128k", "-y", to_path
    ], check=True, capture_output=True)
    with open(to_path, "rb") as f: out = io.BytesIO(f.read())
    os.unlink(ti_path); os.unlink(to_path)
    out.seek(0); return out

async def send_result_msg(ctx, chat_id, result, filename, orig, new, msg):
    saved = orig - new
    pct = round((saved / orig) * 100, 1) if orig > 0 else 0
    sign = "📉" if saved > 0 else "📈"
    caption = (
        f"✅ *Tayyor!*\n\n"
        f"📦 Avval: `{fmt_size(orig)}`\n"
        f"📦 Keyin: `{fmt_size(new)}`\n"
        f"{sign} Tejaldi: `{fmt_size(abs(saved))} ({pct}%)`"
    )
    result.seek(0)
    await ctx.bot.send_document(
        chat_id=chat_id, document=result, filename=filename,
        caption=caption, parse_mode="Markdown"
    )
    try: await msg.delete()
    except: pass

async def send_result(update, ctx, result, filename, orig, new, msg):
    saved = orig - new
    pct = round((saved / orig) * 100, 1) if orig > 0 else 0
    sign = "📉" if saved > 0 else "📈"
    caption = (
        f"✅ *Tayyor!*\n\n"
        f"📦 Avval: `{fmt_size(orig)}`\n"
        f"📦 Keyin: `{fmt_size(new)}`\n"
        f"{sign} Tejaldi: `{fmt_size(abs(saved))} ({pct}%)`"
    )
    result.seek(0)
    await ctx.bot.send_document(
        chat_id=update.effective_chat.id,
        document=result, filename=filename,
        caption=caption, parse_mode="Markdown"
    )
    await msg.delete()

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("settings", settings))
    app.add_handler(CommandHandler("clear", clear_cmd))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VIDEO, handle_video))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info("Bot ishga tushdi!")
    app.run_polling()

if __name__ == "__main__":
    main()
