import discord
import asyncio
from src.utility import login, logout, getImage, get_ticket_num, check_ticket_num, BrowserCriticalError
from src.ticket_records import add_record
import os

async def get_ticket(bot, interaction: discord.Interaction, category, driver_manager, your_web_url, your_account, your_password, target_channel_ids, target_channel_name, maintainer_id_env):

    # 全域狀態檢查
    if bot.ticket_lock.locked():
        # 如果已經 defer 過，要用 followup
        await interaction.followup.send("⚠️ 目前已有票卷正在生成中，請稍候約 5 分鐘再試。", ephemeral=True)
        return

    # interaction.user 取代 ctx.author
    sender_name = interaction.user.display_name

    # 頻道不對的回應，改用 followup 以避免重複回應錯誤
    if str(interaction.channel_id) not in target_channel_ids:
        await interaction.followup.send(f"請在 {target_channel_name} 頻道中發送 /給我{category}票 以索取 {category} QR Code 喔", ephemeral=True)
        return

    welcome_messages_dict = {}
    finish_messages_dict = {}
    logged_in = False
    driver = None

    # 鎖定 driver（同一時間只允許一件事使用瀏覽器）
    await bot.ticket_lock.acquire()
    try:
        driver = await asyncio.to_thread(driver_manager.get_driver)

        await interaction.followup.send(f"{sender_name} 您好，請稍等 30 秒~", ephemeral=True)

        for channel_id in target_channel_ids:
            channel = bot.get_channel(int(channel_id))
            if channel:
                sent_message = await channel.send("努力生成票卷 QR Code 中~~請先不要傳訊息給我，不然我會不理你，稍等大概五分鐘喔！")
                welcome_messages_dict[int(channel_id)] = sent_message
            else:
                print(f'無法找到頻道 {channel_id}')

        if not await login(driver, your_web_url, your_account, your_password):
            await interaction.edit_original_response(content="登入系統時出現問題，請稍後再試")
            channel = bot.get_channel(int(interaction.channel_id))
            if channel:
                await channel.send("登入系統時出現問題")
            return
        logged_in = True

        await getImage(driver, category)

        res_num = get_ticket_num(driver, category)
        if res_num is None:
            await interaction.edit_original_response(content="獲取票卷張數時出現問題，請稍後再試")
            channel = bot.get_channel(int(interaction.channel_id))
            if channel:
                await channel.send("獲取票卷張數時出現問題")
            return
        ticket_num = int(res_num)

        # 確認票卷數量
        if ticket_num < 2:
            await interaction.edit_original_response(content=f"{category} 票卷不足，請等加值後再試><")
            channel = bot.get_channel(int(interaction.channel_id))
            if channel:
                await channel.send(f"{category} 票卷不足，請加值><（剩餘 {ticket_num} 張）")
        else:
            if ticket_num < 6:
                # 頻道廣播訊息依然用 channel.send
                channel = bot.get_channel(int(interaction.channel_id))
                if channel:
                    await channel.send(f"{category} 票卷即將不足，請加值><（剩餘 {ticket_num} 張）")

            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            image_path = os.path.join(base_dir, 'img', 'screenshot_crop.png')
            empty_path = os.path.join(base_dir, 'img', 'empty.png')

            picture = discord.File(image_path, filename="screenshot_crop.png")
            empty_pic = discord.File(empty_path, filename="empty.png")

            # 更新原本的延遲訊息 (edit_original_response)
            await interaction.edit_original_response(content=f"已傳送 {category} QR Code，請在三分鐘之內使用喔", attachments=[picture])

            success = await check_ticket_num(driver, ticket_num, category)
            if success is None:
                # 查票數失敗，使用狀態不明：收回 QR Code 並請使用者找管理員確認
                await interaction.edit_original_response(content="獲取票卷張數時出現問題，請聯絡管理員確認票卷使用狀態", attachments=[empty_pic])
                channel = bot.get_channel(int(interaction.channel_id))
                if channel:
                    await channel.send("獲取票卷張數時出現問題")
                return

            user = await bot.fetch_user(int(maintainer_id_env))

            if success:
                # 更新訊息為成功 (注意 attachments 傳入空列表或新圖片)
                await interaction.edit_original_response(content=f"{sender_name} 已成功使用 {category} 票卷！ \n\n 再請你匯款、Line Pay 或是街口支付了，詳細資訊可以發送 /help 來獲取喔", attachments=[empty_pic])
                ticket_num = ticket_num - 1

                if user:
                    try:
                        sent = await user.send(f"{sender_name} 成功使用 {category} QR Code，剩餘 {ticket_num} 張")
                        try:
                            add_record(interaction.user.id, sender_name, category, sent.id)
                        except Exception as e:
                            print(f"寫入票券紀錄失敗: {e}")
                    except Exception as e:
                        print(f"私訊失敗: {e}")

            else:
                await interaction.edit_original_response(content=f"{sender_name} 未使用 {category} QR Code，請重新生成><", attachments=[empty_pic])

                if user:
                    try:
                        await user.send(f"{sender_name} 未使用 {category} QR Code，剩餘 {ticket_num} 張")
                    except Exception as e:
                        print(f"私訊失敗: {e}")

        for channel_id in target_channel_ids:
            channel = bot.get_channel(int(channel_id))
            if channel:
                sent_message = await channel.send("結束生成。可以再次呼喚我了喔")
                finish_messages_dict[int(channel_id)] = sent_message

    except BrowserCriticalError as e:
        print(f"發生錯誤: {e}")
        # 如果發生錯誤，確保能透過 followup 告知使用者
        try:
            channel = bot.get_channel(int(interaction.channel_id))
            if channel:
                await channel.send("程式執行中發生錯誤，請聯絡管理員。")
        except Exception:
            pass
    except Exception as e:
        print(f"生成票卷時發生錯誤: {e}")
        try:
            await interaction.edit_original_response(content="生成票卷時發生錯誤，請稍後再試，或聯絡管理員")
        except Exception:
            pass
        try:
            channel = bot.get_channel(int(interaction.channel_id))
            if channel:
                await channel.send("生成票卷時發生錯誤，請稍後再試")
        except Exception:
            pass
    finally:
        try:
            # 清理 welcome 訊息（含錯誤與 early return 路徑）
            for sent_message in welcome_messages_dict.values():
                try:
                    await sent_message.delete()
                except Exception as e:
                    print(f"刪除等待訊息失敗: {e}")

            if logged_in:
                if not await logout(driver):
                    print("登出系統時出現問題")

            # 使用背景任務處理 60 秒後的刪除，不影響主邏輯
            async def delayed_delete(msgs):
                await asyncio.sleep(60)
                for m in msgs:
                    try:
                        await m.delete()
                    except Exception:
                        pass

            if finish_messages_dict:
                asyncio.create_task(delayed_delete(finish_messages_dict.values()))
        finally:
            # 無論清理過程發生什麼事都要解鎖，否則整個 bot 會卡死
            bot.ticket_lock.release()
