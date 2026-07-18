# 導入Discord.py模組
import discord
# 導入commands指令模組
from discord.ext import commands
import os

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

from src.get_ticket import get_ticket
from src.utility import BrowserCriticalError, login, logout, get_ticket_num
from src.unpaid_list import is_unpaid, add_unpaid, remove_unpaid, list_unpaid
from src.ticket_records import load_records, mark_reminded
from dotenv import load_dotenv

import re
import time
import asyncio
import datetime

# 1. 建立 Bot 類別來處理同步 (這樣最穩)
class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True  # 確保可以讀取訊息 (on_message 需要)
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # 這行是關鍵：將斜線指令同步到 Discord 伺服器
        await self.tree.sync()
        print("Slash 指令同步完成！")

bot = MyBot()
# driver 的獨占鎖：票卷生成、剩餘票數查詢、healthcheck 探測共用同一個 driver，須互斥
bot.ticket_lock = asyncio.Lock()

# 載入 .env 檔案中的環境變數
load_dotenv()

def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value or not value.strip():
        raise SystemExit(f"缺少環境變數 {name}，請檢查 .env 設定")
    return value.strip()

target_channel_ids = [cid.strip() for cid in _require_env('CHANNEL_IDS').split(',') if cid.strip()]
target_channel_name = _require_env('CHANNEL_NAME')
your_account = _require_env('ACCOUNT')  # NTU COOL 帳號
your_password = _require_env('PASSWORD')  # NTU COOL 密碼
your_web_url = _require_env('URL')  # 租借系統網址
token = _require_env('TOKEN')
maintainer_id_env = _require_env('MAINTAINER_ID')
bot_name_env = _require_env('BOT_NAME')
line_id_env = os.getenv('LINE_ID')  # 選填：欠款提醒與 /help 顯示的 Line ID

# 自我介紹文案（mention 回覆與 welcome 指令共用）
WELCOME_TEXT = f"""我是{bot_name_env}，很高興認識你 ▼・ᴥ・▼

我將提供 **台大游泳池票** 以及** 台大健身中心票** 給你喔~

在 **{target_channel_name}** 頻道中 (っ・Д・)っ

你可以發送 **`/給我游泳池票`** 以索取台大游泳池 QR Code (=^-ω-^=)

或是發送 **`/給我健身中心票`** 以索取台大健身中心 QR Code ฅ^•ﻌ•^ฅ

小小的提醒~~請在給泳驗刷票前就把 QR Code 生成好喔，不然泳驗可能會覺得很奇怪(◉３◉)

也可以發送 **`/help`** 來得到更多資訊喔 (´･ω･`)

運動真的很開心呢，希望能跟大家一起開心游泳和健身 (^_っ^)"""


# ---------- 欠款催繳輔助函式 ----------

def _resolve_qrcode_path():
    """付款 QR 圖檔路徑解析，邏輯照抄 /help 指令：優先用正式檔，不存在就 fallback 範例檔"""
    qrcode_path = os.path.join('img', 'payment_qrcode.png')
    if not os.path.exists(qrcode_path):
        qrcode_path = os.path.join('img', 'payment_qrcode_example.png')
    if not os.path.exists(qrcode_path):
        return None
    return qrcode_path


async def _get_unpaid_summary():
    """
    逐筆核對 data/ticket_records.json 裡的紀錄，在 maintainer DM 上檢查 reaction 狀態。

    回傳 (unresolved, broken, dm)：
      unresolved：尚未解決的紀錄（原始欄位 + age_days，未付款天數）
      broken：dm_message_id 在 maintainer DM 裡已經找不到訊息的紀錄（標記「紀錄異常」）
      dm：maintainer 的 DM channel，供呼叫端重複使用（例如舊格式全文掃描）
    """
    maintainer = await bot.fetch_user(int(maintainer_id_env))
    dm = maintainer.dm_channel or await maintainer.create_dm()
    records = load_records()
    now = datetime.datetime.now(datetime.timezone.utc)

    unresolved = []
    broken = []
    for r in records:
        try:
            msg = await dm.fetch_message(r["dm_message_id"])
        except discord.NotFound:
            broken.append(r)
            continue
        except Exception as e:
            print(f"查詢票券紀錄訊息失敗: {e}")
            broken.append(r)
            continue

        paid = any(str(rx.emoji) == "👍" for rx in msg.reactions)
        unused = any(str(rx.emoji) == "❤️" for rx in msg.reactions)
        if paid or unused:
            continue

        ts = datetime.datetime.fromisoformat(r["ts"])
        age_days = (now - ts).total_seconds() / 86400
        record_with_age = dict(r)
        record_with_age["age_days"] = age_days
        unresolved.append(record_with_age)

    return unresolved, broken, dm


def _group_unpaid_by_user(records):
    """依 user_id 彙總未解決紀錄，回傳 {user_id: {"name", "records", "total", "oldest_age"}}"""
    groups = {}
    for r in records:
        uid = r["user_id"]
        g = groups.setdefault(uid, {"name": r["name"], "records": [], "total": 0, "oldest_age": 0.0})
        g["records"].append(r)
        g["total"] += r.get("amount", 0)
        g["oldest_age"] = max(g["oldest_age"], r["age_days"])
    return groups


async def _reply_in_chunks(message, full_text):
    """Discord 單則訊息上限 2000 字，超過就依行分段發送（沿用 unpaid check 既有的分段模式）"""
    if len(full_text) <= 1900:
        await message.reply(full_text)
        return
    lines = full_text.split("\n")
    chunks = []
    current = ""
    for line in lines:
        if current and len(current) + len(line) + 1 > 1900:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    for chunk in chunks:
        await message.reply(chunk)


def _build_reminder_text(display_name, records):
    """催繳 DM 定稿文案，僅代入變數，不自行更動措辭"""
    lines = []
    total = 0
    for r in records:
        ts = datetime.datetime.fromisoformat(r["ts"]).astimezone()
        lines.append(f"• {ts.strftime('%m/%d')} {r['category']}票 — {r['amount']} 元")
        total += r.get("amount", 0)
    detail = "\n".join(lines)
    line_pay_line = f"• Line Pay:**{line_id_env}**\n" if line_id_env else ""

    return f"""哈囉 {display_name}～我是{bot_name_env} ▼・ᴥ・▼

來小小提醒一下,你最近使用的票卷還沒有完成付款喔 (´･ω･`)

📋 **未付款紀錄:**
{detail}

💰 **合計:{total} 元**

付款方式任選一種:
• 轉帳:**(700) 中華郵政 00814531372557**
{line_pay_line}• 街口支付:掃下面的付款碼

轉帳的話請幫我在備註欄填上 **姓名** 或 **Discord 暱稱** 喔~

付款完成後,麻煩私訊 **冠嘉** 說一聲,確認後就會幫你銷帳囉 ><

如果你其實已經付過款了,或是覺得這筆紀錄怪怪的,也直接跟冠嘉說,馬上幫你查 (=^-ω-^=)"""


async def _handle_unpaid_remind(message, dryrun: bool):
    """unpaid remind / unpaid remind dryrun 共用邏輯"""
    unresolved, _broken, _dm = await _get_unpaid_summary()
    recent = [r for r in unresolved if r["age_days"] < 14]
    groups = _group_unpaid_by_user(recent)

    if not groups:
        await message.reply("✅ 目前沒有 14 天內的待催繳紀錄")
        return

    to_send = []   # [(uid, group)]
    skipped = []   # [(name, reason)]

    for uid, g in groups.items():
        if is_unpaid(uid):
            skipped.append((g["name"], "黑名單"))
            continue

        last_reminded_values = [r.get("last_reminded") for r in g["records"] if r.get("last_reminded")]
        if last_reminded_values:
            latest = max(datetime.datetime.fromisoformat(v) for v in last_reminded_values)
            if (datetime.datetime.now(datetime.timezone.utc) - latest) < datetime.timedelta(days=3):
                skipped.append((g["name"], "3 天內已催過"))
                continue

        to_send.append((uid, g))

    if dryrun:
        preview_sections = [f"🔍 **催繳試跑（Dry Run，將私訊 {len(to_send)} 人）：**"]
        for uid, g in to_send:
            text = _build_reminder_text(g["name"], g["records"])
            preview_sections.append(
                f"👤 **{g['name']}**（`{uid}`）— {len(g['records'])} 筆，{g['total']} 元\n"
                f"───\n{text}\n───"
            )
        if skipped:
            skip_lines = [f"• {name} — {reason}" for name, reason in skipped]
            preview_sections.append("⏭️ **跳過：**\n" + "\n".join(skip_lines))
        await _reply_in_chunks(message, "\n\n".join(preview_sections))
        return

    qrcode_path = _resolve_qrcode_path()
    if qrcode_path is None:
        await message.reply("❌ 找不到付款 QR Code 圖檔，請聯絡管理員")
        return

    sent_list = []
    failed_list = []

    for uid, g in to_send:
        try:
            user = await bot.fetch_user(uid)
            text = _build_reminder_text(g["name"], g["records"])
            # discord.File 不可重用，每次發送都要重新建立
            qrcode_file = discord.File(qrcode_path, filename="payment_qrcode.png")
            await user.send(text, file=qrcode_file)
            mark_reminded(uid)
            sent_list.append((g["name"], len(g["records"]), g["total"]))
        except discord.Forbidden:
            failed_list.append((g["name"], len(g["records"]), g["total"]))
        except Exception as e:
            print(f"催繳私訊失敗: {e}")
            failed_list.append((g["name"], len(g["records"]), g["total"]))

    summary_parts = [f"📨 **催繳結果（共 {len(groups)} 人）：**"]

    if sent_list:
        lines = [f"• {name} — {count} 筆，{total} 元" for name, count, total in sent_list]
        summary_parts.append("✅ **已私訊：**\n" + "\n".join(lines))

    if skipped:
        lines = [f"• {name} — {reason}" for name, reason in skipped]
        summary_parts.append("⏭️ **跳過：**\n" + "\n".join(lines))

    if failed_list:
        lines = []
        for name, count, total in failed_list:
            lines.append(f"• {name} — {count} 筆，{total} 元")
            lines.append("  → 需要手動聯絡")
        summary_parts.append("❌ **私訊失敗（對方可能關閉伺服器成員私訊）：**\n" + "\n".join(lines))

    await _reply_in_chunks(message, "\n\n".join(summary_parts))


class WebDriverManager:
    def __init__(self, options):
        self.options = options
        self.driver = None # 延遲初始化，等真正需要或 healthcheck 時再建立

    def create_driver(self):
        print("正在啟動新的 Chrome 實例...")
        my_service = Service(executable_path="/usr/bin/chromedriver")
        # 這裡不需要 ChromeDriverManager，因為 Dockerfile 已經裝好固定路徑的 Chrome
        return webdriver.Chrome(service=my_service, options=self.options)

    def get_driver(self):
        if self.driver is None:
            self.driver = self.create_driver()
            return self.driver
        
        try:
            # 檢查 driver 是否還能通訊
            _ = self.driver.window_handles 
        except Exception:
            print("偵測到 WebDriver 失效，嘗試重啟...")
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = self.create_driver()
            
        return self.driver

# 初始化 Selenium WebDriver 選項
chrome_options = Options()
chrome_options.add_argument("--headless")  # 啟用無頭模式
chrome_options.add_argument("--disable-gpu")  # 禁用 GPU 渲染
chrome_options.add_argument("--window-size=1920,1080")  # 模擬螢幕解析度
chrome_options.add_argument("--no-sandbox")  # 避免權限問題
chrome_options.add_argument("--disable-dev-shm-usage")  # 避免共享內存不足

chrome_options.binary_location = "/usr/bin/chromium"
# 關鍵：設定遇到所有彈窗自動點擊「確定」
chrome_options.set_capability("unhandledPromptBehavior", "accept")
# 建立 WebDriver 管理實例
driver_manager = WebDriverManager(chrome_options)


async def health_check_task():
    """配合 Docker Healthcheck 使用"""
    while True:
        try:
            if bot.ticket_lock.locked():
                # 票卷生成中 driver 正在忙，不去探測（get_driver 誤判失效會把
                # 使用中的 driver 砍掉重建），bot 本身活著就照常更新心跳
                pass
            else:
                async with bot.ticket_lock:
                    # 檢查 Selenium 是否還活著
                    driver = await asyncio.to_thread(driver_manager.get_driver)
                    _ = await asyncio.to_thread(getattr, driver, "title")

            # 更新心跳檔案
            with open("/tmp/heartbeat", "w") as f:
                f.write(str(time.time()))
        except Exception:
            print("Healthcheck 失敗，停止更新 Heartbeat...")
            # 不更新檔案，Docker 會在幾分鐘後判定容器 Unhealthy 並重啟
        await asyncio.sleep(30)

# 在 on_ready 加入
@bot.event
async def on_ready():
    print(f"{bot.user} 已上線")
    bot.loop.create_task(health_check_task())

@bot.event
async def on_message(message):
    # 防止机器人回复自己
    if message.author == bot.user:
        return

    # 检测消息是否提及了机器人
    if bot.user in message.mentions:
        await message.channel.send(f"{message.author.mention} 你好！{WELCOME_TEXT}")

    # 2. 檢查訊息作者是否為特定使用者
    if int(message.author.id) == int(maintainer_id_env):
        
        # 3. 檢查訊息內容是否與特定觸發訊息相符 (不區分大小寫)
        # 使用 .strip().lower() 處理前後空白和大小寫
        if message.content.strip().lower() == "welcome":
            
            # 4. 取得目標頻道
            for channel_id in target_channel_ids:
                channel = bot.get_channel(int(channel_id))
                if channel :
                    await channel.send(WELCOME_TEXT)
                else:
                    print(f'無法找到頻道 {channel_id}')

# 3. 檢查訊息內容是否與特定觸發訊息相符 (不區分大小寫)
        # 使用 .strip().lower() 處理前後空白和大小寫
        if message.content.strip().lower() == "swim":
            # 4. 取得目標頻道
            for channel_id in target_channel_ids:
                channel = bot.get_channel(int(channel_id))
                if channel :
                    await channel.send(f"""{bot_name_env}的游泳池票卷補足了喔喔好開心 ><
            """
        )
                else:
                    print(f'無法找到頻道 {channel_id}') 
                    
                    
        if message.content.strip().lower() == "gym":
             # 4. 取得目標頻道
            for channel_id in target_channel_ids:
                channel = bot.get_channel(int(channel_id))
                if channel :
                    await channel.send(f"""{bot_name_env}的健身中心票卷補足了喔喔好開心 ><
            """
        )
                else:
                    print(f'無法找到頻道 {channel_id}') 
                    
        if message.content.strip().lower() == "fixed":
             # 4. 取得目標頻道
            for channel_id in target_channel_ids:
                channel = bot.get_channel(int(channel_id))
                if channel :
                    await channel.send(f"""{bot_name_env}回復正常了喔可以呼叫我了喔 ฅ^•ﻌ•^ฅ
            """
        )
                else:
                    print(f'無法找到頻道 {channel_id}')

        # unpaid 欠款名單管理指令
        parts = message.content.strip().split()
        if len(parts) >= 2 and parts[0].lower() == "unpaid":
            sub = parts[1].lower()

            if sub == "add" and len(parts) == 3:
                try:
                    target_id = int(parts[2])
                    target_user = await bot.fetch_user(target_id)
                    username = target_user.display_name if target_user else str(target_id)
                    add_unpaid(target_id, username)
                    await message.reply(f"✅ 已將 **{username}**（`{target_id}`）加入欠款名單")
                except ValueError:
                    await message.reply("❌ 請輸入有效的 User ID，例如：`unpaid add 123456789`")
                except Exception as e:
                    await message.reply(f"❌ 找不到這個 User ID 或發生錯誤：{e}")

            elif sub == "remove" and len(parts) == 3:
                try:
                    target_id = int(parts[2])
                    success = remove_unpaid(target_id)
                    if success:
                        await message.reply(f"✅ 已將 `{target_id}` 從欠款名單移除")
                    else:
                        await message.reply(f"❌ `{target_id}` 不在欠款名單中")
                except ValueError:
                    await message.reply("❌ 請輸入有效的 User ID，例如：`unpaid remove 123456789`")

            elif sub == "list":
                data = list_unpaid()
                if not data:
                    await message.reply("📋 欠款名單目前是空的")
                else:
                    lines = [f"• `{uid}` — {name}" for uid, name in data.items()]
                    await message.reply("📋 **目前欠款名單：**\n" + "\n".join(lines))

            elif sub == "check":
                # 以結構化紀錄檔（data/ticket_records.json）為主，逐筆核對 maintainer DM 的 reaction 狀態
                unresolved, broken, dm = await _get_unpaid_summary()
                groups = _group_unpaid_by_user(unresolved)

                bucket_14 = []
                bucket_14_30 = []
                bucket_30 = []
                for uid, g in groups.items():
                    if g["oldest_age"] >= 30:
                        bucket_30.append((uid, g))
                    elif g["oldest_age"] >= 14:
                        bucket_14_30.append((uid, g))
                    else:
                        bucket_14.append((uid, g))

                sections = []

                if bucket_14:
                    lines = [f"• {g['name']} — {len(g['records'])} 筆，{g['total']} 元" for _, g in bucket_14]
                    sections.append("⏰ **14 天內未付：**\n" + "\n".join(lines))

                if bucket_14_30:
                    lines = [f"• {g['name']} — {len(g['records'])} 筆，{g['total']} 元" for _, g in bucket_14_30]
                    sections.append("⚠️ **14–30 天未付：**\n" + "\n".join(lines))

                if bucket_30:
                    lines = []
                    for uid, g in bucket_30:
                        lines.append(f"• {g['name']} — {len(g['records'])} 筆，{g['total']} 元")
                        lines.append(f"  → 複製執行：unpaid add {uid}")
                    sections.append("🚫 **超過 30 天，建議加入黑名單：**\n" + "\n".join(lines))

                if broken:
                    lines = []
                    for r in broken:
                        ts = datetime.datetime.fromisoformat(r["ts"]).astimezone()
                        lines.append(
                            f"• {r['name']} — {ts.strftime('%m/%d')} {r['category']}票 — {r['amount']} 元"
                            f"（訊息 ID: {r['dm_message_id']}）"
                        )
                    sections.append("❗ **紀錄異常（DM 訊息遺失，需人工確認）：**\n" + "\n".join(lines))

                # fallback：舊格式全文掃描（記錄檔上線前的 DM，dm_message_id 對不到任何紀錄）
                known_ids = {r["dm_message_id"] for r in load_records()}
                cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=14)
                legacy = []
                async for msg in dm.history(after=cutoff, oldest_first=False, limit=300):
                    if msg.author.id != bot.user.id:
                        continue
                    if msg.id in known_ids:
                        continue
                    # 票卷通知是固定格式的單行訊息；bot 自己的 check/remind 回覆是多行，
                    # fullmatch（. 不跨行）天然排除，不再依賴「待催繳」字樣標記
                    if not re.fullmatch(r".+ 成功使用 .+ QR Code，剩餘 \d+ 張", msg.content):
                        continue
                    paid = any(str(rx.emoji) == "👍" for rx in msg.reactions)
                    unused = any(str(rx.emoji) == "❤️" for rx in msg.reactions)
                    if not paid and not unused:
                        legacy.append((msg.created_at, msg.content))

                if legacy:
                    lines = [f"• `{ts.astimezone().strftime('%m/%d')}` {content}" for ts, content in legacy]
                    sections.append("📜 **舊格式紀錄（無法自動催繳）：**\n" + "\n".join(lines))

                if not sections:
                    await message.reply("✅ 目前沒有待催繳紀錄")
                else:
                    await _reply_in_chunks(message, "\n\n".join(sections))

            elif sub == "remind" and len(parts) == 2:
                await _handle_unpaid_remind(message, dryrun=False)

            elif sub == "remind" and len(parts) == 3 and parts[2].lower() == "dryrun":
                await _handle_unpaid_remind(message, dryrun=True)

    # 处理其他命令
    await bot.process_commands(message)

@bot.tree.command(name="給我游泳池票", description="索取台大游泳池票卷 QR Code ><")
async def swimming_ticket(interaction: discord.Interaction):
    # 第一步：立即 defer，這會給機器人 15 分鐘的處理時間
    await interaction.response.defer(ephemeral=True)
    
    # 使用 asyncio.create_task 讓它在背景跑，不阻塞其他指令進入
    asyncio.create_task(
        handle_ticket_request(interaction, "游泳池")
    )

# 修改健身中心指令
@bot.tree.command(name="給我健身中心票", description="索取台大健身中心票卷 QR Code ><")
async def gym_ticket(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    
    asyncio.create_task(
        handle_ticket_request(interaction, "健身中心")
    )

# 定义一个 Slash 命令
# 建立一個統一的處理函式
async def handle_ticket_request(interaction: discord.Interaction, category: str):
    # 檢查是否在欠款名單內
    if is_unpaid(interaction.user.id):
        contact_msg = "• Discord：私訊 **冠嘉**\n"
        if line_id_env:
            contact_msg += f"• Line：**{line_id_env}**\n"
        await interaction.followup.send(
            f"哎呀，你目前還有尚未完成的付款紀錄，暫時無法申請票卷 (´･ω･`)\n\n"
            f"麻煩先完成轉帳後，再透過以下方式通知我確認一下喔：\n\n"
            f"{contact_msg}\n"
            f"確認後就會幫你解除囉，感謝你的配合 ><",
            ephemeral=True
        )
        return

    try:
        # driver 改由 get_ticket 在取得鎖之後才向 driver_manager 索取，
        # 避免生成途中被 healthcheck 或其他指令重建
        await get_ticket(bot, interaction, category, driver_manager, your_web_url, your_account, your_password, target_channel_ids, target_channel_name, maintainer_id_env)
    except BrowserCriticalError:
        print("💥 偵測到致命錯誤，通知管理員並重啟容器...")
        # 可以在這裡加一個發送 Discord 訊息給管理員的邏輯
        import os
        os._exit(1) # 強制結束程式，觸發 Docker restart
    
    

@bot.tree.command(name="剩餘票數", description="查詢游泳池及健身中心剩餘票卷張數")
async def remaining_tickets(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    asyncio.create_task(handle_remaining_check(interaction))

async def handle_remaining_check(interaction: discord.Interaction):
    if str(interaction.channel_id) not in target_channel_ids:
        await interaction.followup.send(f"請在 {target_channel_name} 頻道中使用此指令喔", ephemeral=True)
        return

    if bot.ticket_lock.locked():
        await interaction.followup.send("⚠️ 目前機器人正在處理票卷，無法即時查詢，請稍後再試", ephemeral=True)
        return

    try:
        async with bot.ticket_lock:
            driver = await asyncio.to_thread(driver_manager.get_driver)

            if not await login(driver, your_web_url, your_account, your_password):
                await interaction.followup.send("❌ 登入系統時出現問題，無法查詢票數", ephemeral=True)
                return

            swim_num = await asyncio.to_thread(get_ticket_num, driver, "游泳池")
            gym_num = await asyncio.to_thread(get_ticket_num, driver, "健身中心")

            await logout(driver)

        swim_text = f"**{swim_num}** 張" if swim_num is not None else "查詢失敗"
        gym_text = f"**{gym_num}** 張" if gym_num is not None else "查詢失敗"

        await interaction.followup.send(
            f"**目前剩餘票卷：**\n\n"
            f"🏊 游泳池：{swim_text}\n"
            f"🏋️ 健身中心：{gym_text}",
            ephemeral=True
        )

    except BrowserCriticalError:
        await interaction.followup.send("❌ 系統發生嚴重錯誤，請聯絡管理員", ephemeral=True)
        import os
        os._exit(1)
    except Exception as e:
        print(f"查詢剩餘票數時發生錯誤: {e}")
        await interaction.followup.send("❌ 查詢時發生錯誤，請稍後再試", ephemeral=True)


@bot.tree.command(name="help", description=f"{bot_name_env}怎麼用")
async def ticket(interaction: discord.Interaction):
    if str(interaction.channel_id) in target_channel_ids:
        # 告訴 Discord 正在處理，延遲回應
        await interaction.response.defer(ephemeral=True)

        qrcode_path = os.path.join('img', 'payment_qrcode.png')
        
        # 檢查文件是否存在，防止出錯
        if not os.path.exists(qrcode_path):
            qrcode_path = os.path.join('img', 'payment_qrcode_example.png')
        
        if not os.path.exists(qrcode_path):
            await interaction.followup.send("出錯了，請聯絡管理員")
            return
        
        
       # qrcode = discord.File(qrcode_path, filename="payment_qrcode.png")

        #如果沒有付款碼，請把下面程式碼的註解消除，並註解掉上一行
        qrcode = discord.File(qrcode_path, filename="empty.png")
        
        # 發送消息並附加文件
        await interaction.followup.send(
            f"""請在 **{target_channel_name}** 頻道中

**發送 `/給我游泳池票`** 以索取台大游泳池 QR Code

**發送 `/給我健身中心票`** 以索取台大健身中心 QR Code

**QR Code** 請在三分鐘之內使用

請在給泳驗刷票前就把 QR Code 生成好喔，不然泳驗可能會覺得很奇怪(◉３◉)

台大游泳池票卷費用：**50 元**

健身中心票卷費用：**40  元**

在使用 QR Code 成功後，可以用 **轉帳** 、 **Line Pay** 或是 **街口支付** 

轉帳資料：**(700) 中華郵政 00814531372557**

請幫我在轉帳備註欄填上 **姓名** 或是 **Discord 暱稱** 喔

{f"Line pay 帳號 ID： **{line_id_env}**" if line_id_env else ""}

街口支付可以儲存下面的付款碼，再使用 APP 付款

如果有其他問題，歡迎私訊 **邱冠嘉** (´･ω･`)
            """,
            file=qrcode,
            ephemeral=True
        )
    else:
        await interaction.response.send_message(f"請在 {target_channel_name} 頻道中發送 /help 來獲取使用說明以及匯款資訊喔~", ephemeral=True)

bot.run(token)
