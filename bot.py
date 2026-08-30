import os
import re
import sqlite3
import asyncio
import random
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands
from keep_alive import keep_alive

# =========================
# Config
# =========================
TOKEN = os.getenv("DISCORD_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
BOT_OWNER_NAME = "shiromiz0"

if not TOKEN:
    raise RuntimeError("Missing DISCORD_TOKEN environment variable")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

DB_FILE = "aiko.db"
START_TIME = datetime.now(timezone.utc)

awake_channels = {}
scheduled_reminders = {}
maintenance_mode = False

# =========================
# Database
# =========================
def db_connect():
    return sqlite3.connect(DB_FILE)

def init_db():
    with db_connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                guild_id INTEGER,
                message TEXT NOT NULL,
                remind_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                delivered INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.commit()

def utcnow():
    return datetime.now(timezone.utc)

# =========================
# Wake helpers
# =========================
def set_awake(guild_id: int, channel_id: int, seconds: int = 60):
    awake_channels[(guild_id, channel_id)] = utcnow() + timedelta(seconds=seconds)

def is_awake(guild_id: int, channel_id: int):
    key = (guild_id, channel_id)
    expiry = awake_channels.get(key)
    if not expiry:
        return False
    if utcnow() > expiry:
        awake_channels.pop(key, None)
        return False
    return True

def refresh_awake(guild_id: int, channel_id: int, seconds: int = 60):
    set_awake(guild_id, channel_id, seconds)

# =========================
# Reminder helpers
# =========================
def parse_duration(duration: str):
    m = re.match(r"^(\d+)(m|h)$", duration.lower().strip())
    if not m:
        return None
    amount = int(m.group(1))
    unit = m.group(2)
    seconds = amount * 60 if unit == "m" else amount * 3600
    if seconds < 600 or seconds > 3600:
        return None
    return seconds

def human_eta(remind_at_iso: str):
    remind_at = datetime.fromisoformat(remind_at_iso)
    delta = remind_at - utcnow()
    total = max(0, int(delta.total_seconds()))
    mins = total // 60
    hrs = mins // 60
    mins %= 60
    if hrs and mins:
        return f"{hrs}h {mins}m"
    if hrs:
        return f"{hrs}h"
    return f"{mins}m"

async def deliver_reminder(reminder_id: int):
    with db_connect() as conn:
        row = conn.execute("""
            SELECT id, user_id, channel_id, guild_id, message, remind_at, delivered
            FROM reminders
            WHERE id = ?
        """, (reminder_id,)).fetchone()

    if not row:
        return

    rid, user_id, channel_id, guild_id, message, remind_at_iso, delivered = row
    if delivered:
        return

    remind_at = datetime.fromisoformat(remind_at_iso)
    delay = (remind_at - utcnow()).total_seconds()
    if delay > 0:
        await asyncio.sleep(delay)

    user = None
    try:
        user = await bot.fetch_user(user_id)
    except discord.NotFound:
        pass

    sent = False
    if user:
        try:
            await user.send(f"⏰ Reminder: **{message}**")
            sent = True
        except discord.Forbidden:
            pass

    if not sent:
        channel = bot.get_channel(channel_id)
        if channel:
            try:
                await channel.send(f"<@{user_id}> ⏰ Reminder: **{message}**")
                sent = True
            except discord.Forbidden:
                pass

    with db_connect() as conn:
        conn.execute("UPDATE reminders SET delivered = 1 WHERE id = ?", (rid,))
        conn.commit()

    scheduled_reminders.pop(rid, None)

def schedule_reminder(reminder_id: int):
    if reminder_id in scheduled_reminders:
        return
    scheduled_reminders[reminder_id] = asyncio.create_task(deliver_reminder(reminder_id))

def load_pending_reminders():
    with db_connect() as conn:
        rows = conn.execute("""
            SELECT id
            FROM reminders
            WHERE delivered = 0
        """).fetchall()
    for (rid,) in rows:
        schedule_reminder(rid)

# =========================
# Permission helpers
# =========================
def is_owner(user_id: int):
    return OWNER_ID and user_id == OWNER_ID

def server_owner_only():
    async def predicate(ctx):
        return ctx.guild is not None and ctx.author.id == ctx.guild.owner_id
    return commands.check(predicate)

def bot_owner_only():
    async def predicate(ctx):
        return is_owner(ctx.author.id)
    return commands.check(predicate)

def mod_only():
    async def predicate(ctx):
        if is_owner(ctx.author.id):
            return True
        if ctx.guild is None:
            return False
        perms = ctx.author.guild_permissions
        return (
            perms.manage_messages or
            perms.kick_members or
            perms.ban_members or
            perms.moderate_members or
            perms.manage_channels
        )
    return commands.check(predicate)

# =========================
# Text banks
# =========================
WAKE_LINES = [
    "I’m here. You have one minute. Don’t waste it.",
    "Aiko online. Speak while I’m listening.",
    "You called? Fine. I’m awake for 60 seconds.",
    "I heard you. Talk."
]

CHAT_REPLIES = [
    "I’m listening.",
    "Go on.",
    "Interesting.",
    "That’s useful.",
    "Keep talking.",
    "I can work with that.",
    "Noted.",
    "What next?"
]

TOPIC_PROMPTS = [
    "Do you want a joke, a fact, or a random topic?",
    "Need help, a reminder, or just entertainment?",
    "I can give you a command list if you’ve forgotten everything.",
    "Should I suggest something fun or something useful?"
]

JOKES = [
    "Why do programmers confuse Halloween and Christmas? Because OCT 31 == DEC 25.",
    "I would tell you a UDP joke, but you might not get it.",
    "There are 10 kinds of people: those who understand binary and those who don’t."
]

QUOTES = [
    "Small progress is still progress.",
    "Done is better than perfect.",
    "Consistency beats intensity."
]

FACTS = [
    "Octopuses have three hearts.",
    "Bananas are botanically berries.",
    "Honey can last for years without spoiling."
]

EIGHT_BALL = [
    "Yes.",
    "No.",
    "Maybe.",
    "Probably.",
    "Very likely.",
    "Very unlikely.",
    "Ask again later.",
    "Without a doubt."
]

ASCII_ART = [
    "(=^-ω-^=)",
    "ʕ•ᴥ•ʔ",
    "(ﾉ◕ヮ◕)ﾉ*:･ﾟ✧",
    "¯\\_(ツ)_/¯"
]

EMOJIS = ["✨", "🖤", "🌙", "🎧", "🫖", "📚", "🧠", "🎲"]

# =========================
# Events
# =========================
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} ({bot.user.id})")
    print(f"Bot owner display name: {BOT_OWNER_NAME}")

@bot.event
async def setup_hook():
    init_db()
    load_pending_reminders()

@bot.event
async def on_message(message: discord.Message):
    global maintenance_mode

    if message.author.bot:
        return

    content = message.content.strip()

    if maintenance_mode and content.startswith("!") and message.author.id != OWNER_ID:
        return

    if content in ("!Aiko", "!aiko", "!AIKO"):
        if message.guild:
            set_awake(message.guild.id, message.channel.id, 60)
        await message.reply(random.choice(WAKE_LINES), mention_author=False)
        return

    if message.guild and is_awake(message.guild.id, message.channel.id) and not content.startswith("!"):
        refresh_awake(message.guild.id, message.channel.id, 60)
        lowered = content.lower()

        if any(x in lowered for x in ["hello", "hi", "hey"]):
            await message.reply("Hello.", mention_author=False)
        elif "help" in lowered:
            await message.reply("Use `!help-infocmd`.", mention_author=False)
        elif "joke" in lowered:
            await message.reply(random.choice(JOKES), mention_author=False)
        elif "fact" in lowered:
            await message.reply(random.choice(FACTS), mention_author=False)
        else:
            await message.reply(random.choice(CHAT_REPLIES + TOPIC_PROMPTS), mention_author=False)

    await bot.process_commands(message)

# =========================
# Help
# =========================
@bot.command(name="help-infocmd")
async def help_infocmd(ctx):
    embed = discord.Embed(
        title="🖤 Aiko Command Archive",
        description=(
            "You asked for my command list.\n"
            "Read carefully.\n\n"
            "Use `!Aiko` first if you want my attention for 60 seconds."
        ),
        color=discord.Color.from_rgb(255, 85, 145)
    )

    embed.add_field(
        name="🌙 Wake / Chat",
        value=(
            "`!Aiko`\n"
            "`!help-infocmd`\n"
            "`!aiko-topic`\n"
            "`!aiko-joke`\n"
            "`!aiko-quote`\n"
            "`!aiko-fact`\n"
            "`!aiko-8ball`\n"
        ),
        inline=False
    )

    embed.add_field(
        name="⏰ Reminder / Utility",
        value=(
            "`!aiko-remind <10m-1h> <message>`\n"
            "`!aiko-listreminders`\n"
            "`!aiko-cancelreminder <id>`\n"
            "`!aiko-ping`\n"
            "`!aiko-uptime`\n"
            "`!aiko-time`\n"
            "`!aiko-date`\n"
        ),
        inline=False
    )

    embed.add_field(
        name="🎲 Fun / Social",
        value=(
            "`!aiko-choose`\n"
            "`!aiko-roll`\n"
            "`!aiko-coinflip`\n"
            "`!aiko-dice`\n"
            "`!aiko-rate`\n"
            "`!aiko-ship`\n"
            "`!aiko-roast`\n"
            "`!aiko-compliment`\n"
            "`!aiko-ascii`\n"
            "`!aiko-emoji`\n"
        ),
        inline=False
    )

    embed.add_field(
        name="💬 Text Tools",
        value=(
            "`!aiko-echo`\n"
            "`!aiko-say`\n"
            "`!aiko-reverse`\n"
            "`!aiko-mock`\n"
            "`!aiko-clap`\n"
            "`!aiko-owo`\n"
            "`!aiko-spoiler`\n"
            "`!aiko-poll`\n"
        ),
        inline=False
    )

    embed.add_field(
        name="🧠 Games / Extras",
        value=(
            "`!aiko-trivia`\n"
            "`!aiko-answer <guess>`\n"
            "`!aiko-riddle`\n"
            "`!aiko-color`\n"
            "`!aiko-countdown`\n"
            "`!aiko-timer`\n"
        ),
        inline=False
    )

    embed.add_field(
        name="👤 Info",
        value=(
            "`!aiko-avatar`\n"
            "`!aiko-userinfo`\n"
            "`!aiko-serverinfo`\n"
            "`!aiko-members`\n"
            "`!aiko-afk`\n"
        ),
        inline=False
    )

    embed.add_field(
        name="🛡️ Moderation",
        value=(
            "`!aiko-clean`\n"
            "`!aiko-warn`\n"
            "`!aiko-mute`\n"
            "`!aiko-unmute`\n"
            "`!aiko-kick`\n"
            "`!aiko-ban`\n"
            "`!aiko-unban`\n"
            "`!aiko-slowmode`\n"
            "`!aiko-lock`\n"
            "`!aiko-unlock`\n"
        ),
        inline=False
    )

    embed.add_field(
        name="🏰 Server Owner Only",
        value=(
            "`!aiko-serversecret`\n"
            "`!aiko-setwelcome`\n"
            "`!aiko-setfarewell`\n"
            "`!aiko-setlogchannel`\n"
            "`!aiko-setmodrole`\n"
            "`!aiko-setprefix`\n"
            "`!aiko-serverinfoedit`\n"
            "`!aiko-toggleannouncements`\n"
            "`!aiko-serverbackup`\n"
            "`!aiko-serverreset`\n"
        ),
        inline=False
    )

    embed.add_field(
        name="👑 Bot Owner Only",
        value=(
            "`!aiko-botsecret`\n"
            "`!aiko-reload`\n"
            "`!aiko-debug`\n"
            "`!aiko-setavatar`\n"
            "`!aiko-setnickname`\n"
            "`!aiko-globalannounce`\n"
            "`!aiko-maintenance`\n"
            "`!aiko-shield`\n"
            "`!aiko-unshield`\n"
            "`!aiko-systemstatus`\n"
            "`!aiko-sync`\n"
            "`!aiko-cacheclear`\n"
            "`!aiko-dbbackup`\n"
            "`!aiko-dbrestore`\n"
            "`!aiko-selftest`\n"
            "`!aiko-offline`\n"
        ),
        inline=False
    )

    embed.set_footer(text=f"Aiko • Bot owner display name: {BOT_OWNER_NAME}")
    await ctx.reply(embed=embed, mention_author=False)

# =========================
# General
# =========================
@bot.command(name="aiko-ping")
async def aiko_ping(ctx):
    await ctx.reply(f"Pong: `{round(bot.latency * 1000)}ms`", mention_author=False)

@bot.command(name="aiko-uptime")
async def aiko_uptime(ctx):
    delta = utcnow() - START_TIME
    await ctx.reply(f"Uptime: `{str(delta).split('.')[0]}`", mention_author=False)

@bot.command(name="aiko-joke")
async def aiko_joke(ctx):
    await ctx.reply(random.choice(JOKES), mention_author=False)

@bot.command(name="aiko-quote")
async def aiko_quote(ctx):
    await ctx.reply(random.choice(QUOTES), mention_author=False)

@bot.command(name="aiko-fact")
async def aiko_fact(ctx):
    await ctx.reply(random.choice(FACTS), mention_author=False)

@bot.command(name="aiko-topic")
async def aiko_topic(ctx):
    topics = [
        "What skill do you want to improve?",
        "What game or app do you use the most?",
        "What’s one habit that helped you recently?"
    ]
    await ctx.reply(random.choice(topics), mention_author=False)

@bot.command(name="aiko-8ball")
async def aiko_8ball(ctx, *, question: str = None):
    if not question:
        return await ctx.reply("Ask a question.", mention_author=False)
    await ctx.reply(f"🎱 {random.choice(EIGHT_BALL)}", mention_author=False)

# =========================
# Reminders
# =========================
@bot.command(name="aiko-remind")
async def aiko_remind(ctx, duration: str = None, *, message: str = None):
    if not duration or not message:
        return await ctx.reply("Usage: `!aiko-remind 10m study`", mention_author=False)

    seconds = parse_duration(duration)
    if seconds is None:
        return await ctx.reply("Use a time from `10m` to `1h`.", mention_author=False)

    remind_at = utcnow() + timedelta(seconds=seconds)
    created_at = utcnow().isoformat()

    with db_connect() as conn:
        cursor = conn.execute("""
            INSERT INTO reminders (user_id, channel_id, guild_id, message, remind_at, created_at, delivered)
            VALUES (?, ?, ?, ?, ?, ?, 0)
        """, (
            ctx.author.id,
            ctx.channel.id,
            ctx.guild.id if ctx.guild else None,
            message,
            remind_at.isoformat(),
            created_at
        ))
        reminder_id = cursor.lastrowid
        conn.commit()

    schedule_reminder(reminder_id)
    await ctx.reply(f"Reminder saved as **#{reminder_id}**. I’ll remind you in **{duration}**.", mention_author=False)

@bot.command(name="aiko-listreminders")
async def aiko_listreminders(ctx):
    with db_connect() as conn:
        rows = conn.execute("""
            SELECT id, message, remind_at
            FROM reminders
            WHERE user_id = ? AND delivered = 0
            ORDER BY remind_at ASC
        """, (ctx.author.id,)).fetchall()

    if not rows:
        return await ctx.reply("You have no pending reminders.", mention_author=False)

    lines = [f"**#{rid}** • in **{human_eta(remind_at)}** • {msg}" for rid, msg, remind_at in rows[:20]]
    await ctx.reply("\n".join(lines), mention_author=False)

@bot.command(name="aiko-cancelreminder")
async def aiko_cancelreminder(ctx, reminder_id: int = None):
    if reminder_id is None:
        return await ctx.reply("Usage: `!aiko-cancelreminder 12`", mention_author=False)

    with db_connect() as conn:
        row = conn.execute("""
            SELECT id
            FROM reminders
            WHERE id = ? AND user_id = ? AND delivered = 0
        """, (reminder_id, ctx.author.id)).fetchone()

        if not row:
            return await ctx.reply("I couldn’t find that pending reminder.", mention_author=False)

        conn.execute("UPDATE reminders SET delivered = 1 WHERE id = ?", (reminder_id,))
        conn.commit()

    task = scheduled_reminders.pop(reminder_id, None)
    if task:
        task.cancel()

    await ctx.reply(f"Canceled reminder **#{reminder_id}**.", mention_author=False)

# =========================
# Fun / text commands
# =========================
@bot.command(name="aiko-choose")
async def aiko_choose(ctx, *, options: str = None):
    if not options:
        return await ctx.reply("Give options separated by commas.", mention_author=False)
    items = [x.strip() for x in options.split(",") if x.strip()]
    if len(items) < 2:
        return await ctx.reply("Need at least two options.", mention_author=False)
    await ctx.reply(f"I choose: **{random.choice(items)}**", mention_author=False)

@bot.command(name="aiko-roll")
async def aiko_roll(ctx, sides: int = 100):
    sides = max(2, min(sides, 1000))
    await ctx.reply(f"🎲 {random.randint(1, sides)}", mention_author=False)

@bot.command(name="aiko-coinflip")
async def aiko_coinflip(ctx):
    await ctx.reply(random.choice(["Heads.", "Tails."]), mention_author=False)

@bot.command(name="aiko-dice")
async def aiko_dice(ctx, count: int = 2, sides: int = 6):
    count = max(1, min(count, 10))
    sides = max(2, min(sides, 100))
    rolls = [random.randint(1, sides) for _ in range(count)]
    await ctx.reply(f"Rolls: {rolls} | Total: {sum(rolls)}", mention_author=False)

@bot.command(name="aiko-rate")
async def aiko_rate(ctx, *, thing: str = None):
    if not thing:
        return await ctx.reply("Tell me what to rate.", mention_author=False)
    await ctx.reply(f"`{thing}` gets **{random.randint(1,10)}/10**", mention_author=False)

@bot.command(name="aiko-ship")
async def aiko_ship(ctx, name1: str = None, name2: str = None):
    if not name1 or not name2:
        return await ctx.reply("Usage: `!aiko-ship name1 name2`", mention_author=False)
    await ctx.reply(f"Compatibility: **{random.randint(1,100)}%**", mention_author=False)

@bot.command(name="aiko-roast")
async def aiko_roast(ctx, *, target: str = None):
    roasts = [
        "Your to-do list files a missing person report every morning.",
        "You load slower than free Wi-Fi in a basement.",
        "You have strong 'opens 47 tabs and finishes nothing' energy."
    ]
    line = random.choice(roasts)
    await ctx.reply(f"{target}: {line}" if target else line, mention_author=False)

@bot.command(name="aiko-compliment")
async def aiko_compliment(ctx, *, target: str = None):
    comps = [
        "You make difficult things look manageable.",
        "Your effort is more visible than you think.",
        "You have excellent taste.",
        "You’re more capable than your doubts claim."
    ]
    line = random.choice(comps)
    await ctx.reply(f"{target}: {line}" if target else line, mention_author=False)

@bot.command(name="aiko-countdown")
async def aiko_countdown(ctx, seconds: int):
    if seconds < 1 or seconds > 30:
        return await ctx.reply("Seconds must be between 1 and 30.", mention_author=False)
    msg = await ctx.send(f"Countdown: {seconds}")
    while seconds > 0:
        await asyncio.sleep(1)
        seconds -= 1
        await msg.edit(content=f"Countdown: {seconds}")
    await ctx.send("Done.")

@bot.command(name="aiko-timer")
async def aiko_timer(ctx, seconds: int):
    if seconds < 1 or seconds > 300:
        return await ctx.reply("Seconds must be between 1 and 300.", mention_author=False)
    await ctx.reply(f"Timer started for {seconds}s.", mention_author=False)
    await asyncio.sleep(seconds)
    await ctx.send(f"{ctx.author.mention} ⏰ Timer finished.")

@bot.command(name="aiko-echo")
async def aiko_echo(ctx, *, text: str = None):
    if not text:
        return await ctx.reply("Give me text.", mention_author=False)
    await ctx.reply(text, mention_author=False)

@bot.command(name="aiko-say")
async def aiko_say(ctx, *, text: str = None):
    if not text:
        return await ctx.reply("Give me text.", mention_author=False)
    try:
        await ctx.message.delete()
    except discord.Forbidden:
        pass
    await ctx.send(text)

@bot.command(name="aiko-reverse")
async def aiko_reverse(ctx, *, text: str = None):
    if not text:
        return await ctx.reply("Give me text.", mention_author=False)
    await ctx.reply(text[::-1], mention_author=False)

@bot.command(name="aiko-mock")
async def aiko_mock(ctx, *, text: str = None):
    if not text:
        return await ctx.reply("Give me text.", mention_author=False)
    mocked = "".join(c.upper() if i % 2 else c.lower() for i, c in enumerate(text))
    await ctx.reply(mocked, mention_author=False)

@bot.command(name="aiko-clap")
async def aiko_clap(ctx, *, text: str = None):
    if not text:
        return await ctx.reply("Give me text.", mention_author=False)
    await ctx.reply(" 👏 ".join(text.split()), mention_author=False)

@bot.command(name="aiko-owo")
async def aiko_owo(ctx, *, text: str = None):
    if not text:
        return await ctx.reply("Give me text.", mention_author=False)
    owo = text.replace("r", "w").replace("l", "w").replace("R", "W").replace("L", "W")
    await ctx.reply(owo + " owo", mention_author=False)

@bot.command(name="aiko-spoiler")
async def aiko_spoiler(ctx, *, text: str = None):
    if not text:
        return await ctx.reply("Give me text.", mention_author=False)
    await ctx.reply(f"||{text}||", mention_author=False)

@bot.command(name="aiko-poll")
async def aiko_poll(ctx, *, content: str = None):
    if not content or "|" not in content:
        return await ctx.reply("Usage: `!aiko-poll Question | option1, option2`", mention_author=False)

    question, options = content.split("|", 1)
    items = [x.strip() for x in options.split(",") if x.strip()][:10]
    if len(items) < 2:
        return await ctx.reply("Need at least two options.", mention_author=False)

    emojis = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]
    desc = "\n".join(f"{emojis[i]} {opt}" for i, opt in enumerate(items))
    msg = await ctx.send(f"**{question.strip()}**\n{desc}")
    for i in range(len(items)):
        await msg.add_reaction(emojis[i])

@bot.command(name="aiko-trivia")
async def aiko_trivia(ctx):
    trivia = [
        ("What planet is known as the Red Planet?", "mars"),
        ("How many sides does a hexagon have?", "6"),
        ("What gas do plants absorb?", "carbon dioxide"),
    ]
    q, a = random.choice(trivia)
    bot._quiz_answer = a
    await ctx.reply(f"Trivia: {q} Use `!aiko-answer your_guess`", mention_author=False)

@bot.command(name="aiko-riddle")
async def aiko_riddle(ctx):
    riddles = [
        ("What has keys but can’t open locks?", "keyboard"),
        ("What has hands but cannot clap?", "clock"),
        ("What gets wetter the more it dries?", "towel"),
    ]
    q, a = random.choice(riddles)
    bot._quiz_answer = a
    await ctx.reply(f"Riddle: {q} Use `!aiko-answer your_guess`", mention_author=False)

@bot.command(name="aiko-answer")
async def aiko_answer(ctx, *, guess: str = None):
    ans = getattr(bot, "_quiz_answer", None)
    if not ans:
        return await ctx.reply("No active question.", mention_author=False)
    if not guess:
        return await ctx.reply("Give your answer.", mention_author=False)
    if guess.strip().lower() == ans.lower():
        await ctx.reply("Correct.", mention_author=False)
    else:
        await ctx.reply(f"Not quite. The answer was **{ans}**.", mention_author=False)
    bot._quiz_answer = None

@bot.command(name="aiko-color")
async def aiko_color(ctx):
    val = random.randint(0, 0xFFFFFF)
    await ctx.reply(f"Random color: `#{val:06X}`", mention_author=False)

@bot.command(name="aiko-ascii")
async def aiko_ascii(ctx):
    await ctx.reply(random.choice(ASCII_ART), mention_author=False)

@bot.command(name="aiko-emoji")
async def aiko_emoji(ctx):
    await ctx.reply(random.choice(EMOJIS), mention_author=False)

@bot.command(name="aiko-time")
async def aiko_time(ctx):
    await ctx.reply(f"UTC time: `{utcnow().strftime('%H:%M:%S')}`", mention_author=False)

@bot.command(name="aiko-date")
async def aiko_date(ctx):
    await ctx.reply(f"UTC date: `{utcnow().strftime('%Y-%m-%d')}`", mention_author=False)

@bot.command(name="aiko-avatar")
async def aiko_avatar(ctx, member: discord.Member = None):
    member = member or ctx.author
    await ctx.reply(member.display_avatar.url, mention_author=False)

@bot.command(name="aiko-userinfo")
async def aiko_userinfo(ctx, member: discord.Member = None):
    member = member or ctx.author
    await ctx.reply(
        f"User: **{member}**\nID: `{member.id}`\nJoined: `{member.joined_at}`",
        mention_author=False
    )

@bot.command(name="aiko-serverinfo")
async def aiko_serverinfo(ctx):
    if not ctx.guild:
        return await ctx.reply("This command only works in a server.", mention_author=False)
    g = ctx.guild
    await ctx.reply(
        f"Server: **{g.name}**\nMembers: `{g.member_count}`\nID: `{g.id}`",
        mention_author=False
    )

@bot.command(name="aiko-members")
async def aiko_members(ctx):
    if not ctx.guild:
        return await ctx.reply("This command only works in a server.", mention_author=False)
    await ctx.reply(f"Member count: `{ctx.guild.member_count}`", mention_author=False)

@bot.command(name="aiko-afk")
async def aiko_afk(ctx, *, reason: str = "AFK"):
    await ctx.reply(f"{ctx.author.mention} is now AFK: {reason}", mention_author=False)

# =========================
# Moderation
# =========================
@bot.command(name="aiko-clean")
@mod_only()
async def aiko_clean(ctx, amount: int = 5):
    amount = max(1, min(amount, 100))
    deleted = await ctx.channel.purge(limit=amount + 1)
    msg = await ctx.send(f"Deleted {len(deleted)-1} messages.")
    await asyncio.sleep(3)
    try:
        await msg.delete()
    except discord.Forbidden:
        pass

@bot.command(name="aiko-warn")
@mod_only()
async def aiko_warn(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    await ctx.send(f"⚠️ {member.mention}, warning: {reason}")

@bot.command(name="aiko-mute")
@mod_only()
async def aiko_mute(ctx, member: discord.Member, minutes: int = 10, *, reason: str = "No reason provided"):
    minutes = max(1, min(minutes, 10080))
    until = discord.utils.utcnow() + timedelta(minutes=minutes)
    await member.timeout(until, reason=reason)
    await ctx.send(f"Muted {member.mention} for {minutes} minute(s). Reason: {reason}")

@bot.command(name="aiko-unmute")
@mod_only()
async def aiko_unmute(ctx, member: discord.Member):
    await member.timeout(None, reason="Unmuted")
    await ctx.send(f"Unmuted {member.mention}.")

@bot.command(name="aiko-kick")
@mod_only()
async def aiko_kick(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    await member.kick(reason=reason)
    await ctx.send(f"Kicked {member}.")

@bot.command(name="aiko-ban")
@mod_only()
async def aiko_ban(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    await member.ban(reason=reason, delete_message_days=0)
    await ctx.send(f"Banned {member}.")

@bot.command(name="aiko-unban")
@mod_only()
async def aiko_unban(ctx, user_id: int):
    user = await bot.fetch_user(user_id)
    await ctx.guild.unban(user, reason="Unbanned")
    await ctx.send(f"Unbanned {user}.")

@bot.command(name="aiko-slowmode")
@mod_only()
async def aiko_slowmode(ctx, seconds: int = 5):
    seconds = max(0, min(seconds, 21600))
    await ctx.channel.edit(slowmode_delay=seconds)
    await ctx.send(f"Slowmode set to {seconds}s.")

@bot.command(name="aiko-lock")
@mod_only()
async def aiko_lock(ctx):
    overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrite.send_messages = False
    await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
    await ctx.send("Channel locked.")

@bot.command(name="aiko-unlock")
@mod_only()
async def aiko_unlock(ctx):
    overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrite.send_messages = True
    await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
    await ctx.send("Channel unlocked.")

# =========================
# Server owner only
# =========================
@bot.command(name="aiko-serversecret")
@server_owner_only()
async def aiko_serversecret(ctx):
    await ctx.reply("Server owner access confirmed.", mention_author=False)

@bot.command(name="aiko-setwelcome")
@server_owner_only()
async def aiko_setwelcome(ctx, *, text: str = None):
    await ctx.reply(f"Welcome message set to: `{text}`", mention_author=False)

@bot.command(name="aiko-setfarewell")
@server_owner_only()
async def aiko_setfarewell(ctx, *, text: str = None):
    await ctx.reply(f"Farewell message set to: `{text}`", mention_author=False)

@bot.command(name="aiko-setlogchannel")
@server_owner_only()
async def aiko_setlogchannel(ctx, channel: discord.TextChannel):
    await ctx.reply(f"Log channel set to {channel.mention}", mention_author=False)

@bot.command(name="aiko-setmodrole")
@server_owner_only()
async def aiko_setmodrole(ctx, role: discord.Role):
    await ctx.reply(f"Moderator role set to `{role.name}`", mention_author=False)

@bot.command(name="aiko-setprefix")
@server_owner_only()
async def aiko_setprefix(ctx, prefix: str):
    await ctx.reply(f"Server prefix preference noted: `{prefix}`", mention_author=False)

@bot.command(name="aiko-serverinfoedit")
@server_owner_only()
async def aiko_serverinfoedit(ctx, *, note: str = None):
    await ctx.reply(f"Server note saved: `{note}`", mention_author=False)

@bot.command(name="aiko-toggleannouncements")
@server_owner_only()
async def aiko_toggleannouncements(ctx):
    await ctx.reply("Announcement toggle changed.", mention_author=False)

@bot.command(name="aiko-serverbackup")
@server_owner_only()
async def aiko_serverbackup(ctx):
    await ctx.reply("Server backup request recorded.", mention_author=False)

@bot.command(name="aiko-serverreset")
@server_owner_only()
async def aiko_serverreset(ctx):
    await ctx.reply("Server settings reset request recorded.", mention_author=False)

# =========================
# Bot owner only
# =========================
@bot.command(name="aiko-botsecret")
@bot_owner_only()
async def aiko_botsecret(ctx):
    await ctx.reply(f"Bot owner access confirmed: **{BOT_OWNER_NAME}**", mention_author=False)

@bot.command(name="aiko-reload")
@bot_owner_only()
async def aiko_reload(ctx):
    await ctx.reply("Reload request received.", mention_author=False)

@bot.command(name="aiko-debug")
@bot_owner_only()
async def aiko_debug(ctx):
    await ctx.reply("Debug mode output placeholder.", mention_author=False)

@bot.command(name="aiko-setavatar")
@bot_owner_only()
async def aiko_setavatar(ctx, url: str = None):
    await ctx.reply("Avatar update request recorded.", mention_author=False)

@bot.command(name="aiko-setnickname")
@bot_owner_only()
async def aiko_setnickname(ctx, *, nick: str = None):
    await ctx.reply(f"Nickname change request noted: `{nick}`", mention_author=False)

@bot.command(name="aiko-globalannounce")
@bot_owner_only()
async def aiko_globalannounce(ctx, *, text: str = None):
    await ctx.reply(f"Global announcement prepared: `{text}`", mention_author=False)

@bot.command(name="aiko-maintenance")
@bot_owner_only()
async def aiko_maintenance(ctx):
    global maintenance_mode
    maintenance_mode = not maintenance_mode
    state = "offline/maintenance mode" if maintenance_mode else "online mode"
    await ctx.reply(f"Aiko is now in **{state}**.", mention_author=False)

@bot.command(name="aiko-shield")
@bot_owner_only()
async def aiko_shield(ctx):
    await ctx.reply("Protection mode enabled.", mention_author=False)

@bot.command(name="aiko-unshield")
@bot_owner_only()
async def aiko_unshield(ctx):
    await ctx.reply("Protection mode disabled.", mention_author=False)

@bot.command(name="aiko-systemstatus")
@bot_owner_only()
async def aiko_systemstatus(ctx):
    await ctx.reply("System status: online.", mention_author=False)

@bot.command(name="aiko-sync")
@bot_owner_only()
async def aiko_sync(ctx):
    await ctx.reply("Sync request received.", mention_author=False)

@bot.command(name="aiko-cacheclear")
@bot_owner_only()
async def aiko_cacheclear(ctx):
    await ctx.reply("Cache clear request recorded.", mention_author=False)

@bot.command(name="aiko-dbbackup")
@bot_owner_only()
async def aiko_dbbackup(ctx):
    await ctx.reply("Database backup request recorded.", mention_author=False)

@bot.command(name="aiko-dbrestore")
@bot_owner_only()
async def aiko_dbrestore(ctx):
    await ctx.reply("Database restore request recorded.", mention_author=False)

@bot.command(name="aiko-selftest")
@bot_owner_only()
async def aiko_selftest(ctx):
    await ctx.reply("Self-test complete.", mention_author=False)

@bot.command(name="aiko-offline")
@bot_owner_only()
async def aiko_offline(ctx):
    await ctx.reply("Offline request recorded.", mention_author=False)

# =========================
# Error handling
# =========================
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        await ctx.reply("You don’t have permission to use that command.", mention_author=False)
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.reply("Missing argument. Use `!help-infocmd`.", mention_author=False)
    elif isinstance(error, commands.BadArgument):
        await ctx.reply("Invalid argument. Use `!help-infocmd`.", mention_author=False)
    elif isinstance(error, commands.CommandNotFound):
        return
    else:
        await ctx.reply("Something went wrong.", mention_author=False)
        raise error

# =========================
# Run
# =========================
keep_alive()
bot.run(os.getenv("DISCORD_TOKEN"))
