# discord_ticket_bot

<!-- GETTING STARTED -->
## Getting Started

This repository is for NTU Lifeguard. The repo creates a chatbot, which is powered by Docker, Selenium and Discord API. By sending messages in specific Discord channel, the chatbot will generate QRCode for NTU swimming pool and NTU fitness center.

## Usage Example
![](examples/usage.gif)

## Usage 
1. The slash command will generate QR Code for NTU swimming pool
   ```sh
   /給我游泳池票
   ```

2. The slash command will generate QR Code for NTU gym
   ```sh
   /給我健身中心票
   ```

3. The slash command will generate the instruction of the bot for discord users.
   ```sh
   /help
   ```

## 🛠 Prerequisites

Before setting up the project, please ensure you have the following installed:

- [ ] **[Docker](https://www.docker.com/products/docker-desktop/)** > **Tip:** It is highly recommended to enable **"Start Docker Desktop when you sign in"** in the settings to ensure the bot starts automatically with your system.
- [ ] **WSL 2** (Windows Users Only)  
  Required to run Docker containers efficiently on Windows. [Follow the official Installation Guide](https://learn.microsoft.com/en-us/windows/wsl/install).
- [ ] **(Optional) Remote Desktop** Tools like **AnyDesk** or **TeamViewer** are useful if you need to manually restart or monitor the bot from a different location.


## Installation

1. Clone the repo
   ```sh
   git clone https://github.com/ycchang0324/discord_ticket_bot
   ```

2. Create a Discord chatbot, for more details please check [here](https://discord.com/developers/docs/intro).

The setting for discord bot is shown in the following picture.
![](examples/bot_setting.png)

3. Add the payment QRCode as payment_qrcode.png in img folder.

4. Create .env file, copy the text in .env.example and fill in
(1) Discord channel IDs(can be multiple, separated by colons)
(2) Discord channel name(only for main channel's name)
(3) NTU account
(4) NTU password
(5) NTU rental system sso url: https://rent.pe.ntu.edu.tw/sso2_go.php?BUrl=
(6) Discord bot token.
(7) Maintainer's ID
(8) Bot Name
(9) (Optional) Line ID — if provided, it will be shown in the payment reminder message sent to unpaid users

5. Edit the payment message
```python
await ctx.followup.send("...", file=qrcode, ephemeral=True)
```
, which is in the function
```python
@bot.slash_command(name="help", description="呆呆獸怎麼用")
```
in main.py. Please notice that if the payment QR code is not needed,  please replace the QR code picture by empty picture by uncomment the code
```python
qrcode = discord.File(qrcode_path, filename="empty.png")
```

## Deployment

1. change directory to the folder
   ```sh
   cd discord_ticket_bot
   ```

2. build the image
   ```sh
   docker build --progress=plain --no-cache -t discord-ticket-bot .
   ```

3. run the container(every time you edit the code)
   ```sh
   docker-compose up -d --build
   ```  

## Maintainer's prompts

The mainainer can send specific messages to the bot either privately or publicly.

1.    
```sh
   welcome
```

The bot will send the welcome message to the channel.

2.    
```sh
   swim
```

The bot will send the message that the swimming tickets is full.

3.    
```sh
   gym
```

The bot will send the message that the gym tickets is full.

4.
```sh
   fixed
```

The bot will send the message that the bot has been fixed.

5.
```sh
   unpaid add <user_id>
```

Add a user to the unpaid list by their Discord User ID. The user will be blocked from requesting tickets until removed.
To find a user's ID: enable Developer Mode in Discord settings, then right-click the user and select "Copy User ID".

6.
```sh
   unpaid remove <user_id>
```

Remove a user from the unpaid list, restoring their ability to request tickets.

7.
```sh
   unpaid list
```

Display all users currently on the unpaid list.

8.
```sh
   unpaid check
```

List all unpaid ticket records, grouped by age:
- **⏰ Within 14 days** — will be covered by `unpaid remind`
- **⚠️ 14–30 days** — shown for manual follow-up
- **🚫 Over 30 days** — a ready-to-copy `unpaid add <user_id>` command is attached, suggesting a blacklist

Records are read from `data/ticket_records.json` (written automatically on every successful ticket use since this feature landed) and cross-checked against the reactions on the bot's DM notifications. Old-format DMs (before the record file existed) are still scanned as a fallback and listed separately.

**Reaction convention:**
- 👍 — payment received (skip)
- ❤️ — ticket unused / no charge needed (skip)
- *(no reaction)* — pending payment, will appear in the result

9.
```sh
   unpaid remind dryrun
```

Preview who would receive a payment reminder DM and the exact message content, **without sending anything**. Always run this first.

10.
```sh
   unpaid remind
```

Send a payment reminder DM (itemized records, total amount, payment info and the JKO Pay QR code) to every user with unpaid records within the last 14 days. Users on the blacklist, or already reminded within the last 3 days, are skipped automatically. A summary (sent / skipped / failed) is replied to the maintainer — users whose DMs are closed appear in the failed list and need manual contact.

11.
```sh
   help
```

**(DM only)** Show the maintainer command cheat sheet — all the commands above plus the reaction convention. This is separate from the public `/help` slash command used in the channel.

---

> **Fork Note:** The entire `unpaid` command series (`add`, `remove`, `list`, `check`, `remind`) and the structured ticket record system (`data/ticket_records.json`) were added in this fork and co-authored with [Claude](https://claude.ai) (Anthropic).

<!-- CONTACT -->
## Contact

Yuan-Chia, Chang - ycchang0324@gmail.com

Project Link: [https://github.com/ycchang0324/discord_ticket_bot](https://github.com/ycchang0324/discord_ticket_bot)

