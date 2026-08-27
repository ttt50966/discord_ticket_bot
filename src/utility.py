
import asyncio
import logging
import os
import re
import time
from datetime import datetime

from PIL import Image
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    UnexpectedAlertPresentException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# 獲取當前 Python 檔案的路徑
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)

# 設定保存的子資料夾和檔名
log_dir = os.path.join(parent_dir, 'log')
error_path = os.path.join(log_dir, 'error.txt')

# 如果目錄不存在，則創建該目錄
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

# 如果檔案不存在，則創建空檔案
if not os.path.exists(error_path):
    with open(error_path, 'w') as f:
        pass

# 配置 logging 模組，將日誌寫入 error.txt 文件
logging.basicConfig(filename=error_path, level=logging.ERROR, 
                    format='%(asctime)s - %(levelname)s - %(message)s')

class BrowserCriticalError(Exception):
    """當 WebDriver 完全無法通訊或環境損壞時拋出"""


class LoginRejected(Exception):
    """ADFS 明確回報帳密錯誤／帳號停用，重試沒有意義"""


# ADFS 登入失敗頁的錯誤字樣（中英文介面都可能出現）
_ADFS_ERROR_HINTS = (
    "識別碼或密碼不正確",
    "密碼不正確",
    "帳號或密碼錯誤",
    "您的帳戶已被鎖定",
    "Incorrect user ID or password",
    "The user name or password is incorrect",
    "Your account is locked",
)


def _adfs_error_text(driver):
    """若停在 ADFS 且頁面出現錯誤訊息，回傳該訊息；否則回傳 None"""
    if "adfs.ntu.edu.tw" not in (driver.current_url or ""):
        return None
    for selector in ("#errorText", "#error", ".error", "#errorMessage"):
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, selector):
                text = (el.text or "").strip()
                if text:
                    return text
        except WebDriverException:
            continue
    try:
        body = driver.find_element(By.TAG_NAME, "body").text or ""
    except WebDriverException:
        return None
    for hint in _ADFS_ERROR_HINTS:
        if hint in body:
            return hint
    return None




# 錯誤診斷函數
def handle_error_diagnostics(driver, error_summary):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs("/app/log", exist_ok=True)
    screenshot_path = f"/app/log/login_error_{timestamp}.png"
    try:
        current_url = driver.current_url
        driver.save_screenshot(screenshot_path)
        log_msg = f"{error_summary} | 網址: {current_url} | 截圖: {screenshot_path}"
        logging.error(log_msg)
        print(log_msg, flush=True)
    except Exception as e:
        print(f"診斷失敗: {e}", flush=True)


def _login_once(driver, url, account, password):
    """單次登入嘗試（同步阻塞，須經 asyncio.to_thread 呼叫），成功回傳 True，失敗回傳 False。"""
    stage = "初始化"
    try:
        # 1. 載入 URL
        stage = "載入 SSO 頁面"
        driver.set_page_load_timeout(120)
        try:
            driver.get(url)
        except TimeoutException:
            driver.execute_script("window.stop();")

        wait = WebDriverWait(driver, 120, poll_frequency=0.5)

        # 2. 等待 ADFS 載入
        stage = "等待 ADFS 頁面載入"
        wait.until(lambda d: d.current_url and "adfs.ntu.edu.tw" in d.current_url)

        # 3. 填寫帳號
        stage = "填寫帳號"
        user_input = wait.until(EC.presence_of_element_located((By.NAME, "ctl00$ContentPlaceHolder1$UsernameTextBox")))
        user_input.clear()
        user_input.send_keys(account)

        stage = "填寫密碼"
        pass_input = driver.find_element(By.NAME, "ctl00$ContentPlaceHolder1$PasswordTextBox")
        pass_input.clear()
        pass_input.send_keys(password)

        # 4. 提交
        stage = "點擊提交登入"
        login_btn = driver.find_element(By.NAME, "ctl00$ContentPlaceHolder1$SubmitButton")
        driver.execute_script("arguments[0].click();", login_btn)

        # 4.5 新版 SSO/ADFS 成功後會跳 JS alert（例如「帳號認證完成。」）。
        # unhandledPromptBehavior=accept 在新版 chromedriver 仍會對「當下正在執行的
        # 指令」丟 UnexpectedAlertPresentException，所以這裡顯式等待並接受一次。
        stage = "接受登入確認彈窗"
        try:
            WebDriverWait(driver, 15, poll_frequency=0.3).until(EC.alert_is_present())
            _a = driver.switch_to.alert
            _txt = _a.text
            _a.accept()
            print(f"ℹ️ 已接受登入彈窗：{_txt}", flush=True)
        except TimeoutException:
            pass

        # 5. 驗證回傳
        stage = "驗證登入結果回傳"

        def check_login_success(d):
            """只認「已離開 ADFS 且落在租借系統會員頁」。

            起始 URL（sso2_go.php?BUrl=…）與 ADFS 頁的回傳參數本身就含
            rent.pe.ntu.edu.tw，只比對 hostname 會讓停在 ADFS 錯誤頁也回報成功。
            """
            try:
                url = d.current_url or ""
            except UnexpectedAlertPresentException:
                try:
                    d.switch_to.alert.accept()
                except Exception:
                    pass
                return False
            if not url:
                return False
            if "adfs.ntu.edu.tw" not in url and "sso2_go.php" not in url and "member" in url.lower():
                return True
            err = _adfs_error_text(d)
            if err:
                raise LoginRejected(err)
            return False

        wait.until(check_login_success)

        print(f"✅ [{datetime.now()}] 登入成功（已跳轉至會員頁面）", flush=True)
        return True

    except LoginRejected as e:
        handle_error_diagnostics(driver, f"⛔ 登入被拒（帳密錯誤或帳號停用），不再重試: {e!s}")
        raise

    except Exception as e:
        handle_error_diagnostics(driver, f"❌ 登入失敗於 [{stage}]: {e!s}")
        return False


async def login(driver, url, account, password, max_retries=3, retry_delay=5):
    """帶自動重試的登入函數，適合網路不穩的環境（例如樹莓派 WiFi）。"""
    for attempt in range(1, max_retries + 1):
        print(f"🔄 登入嘗試 {attempt}/{max_retries}...", flush=True)
        try:
            ok = await asyncio.to_thread(_login_once, driver, url, account, password)
        except LoginRejected as e:
            # 帳密錯誤／帳號停用，重試只是浪費 driver 時間，直接放棄
            print(f"⛔ 登入被拒，放棄重試：{e}", flush=True)
            return False
        if ok:
            return True
        if attempt < max_retries:
            print(f"⏳ 登入失敗，{retry_delay} 秒後重試...", flush=True)
            await asyncio.sleep(retry_delay)
    print(f"❌ 已達最大重試次數 ({max_retries})，登入放棄。", flush=True)
    return False


def _logout_sync(driver):
    try:
        if driver.session_id is None:
            raise WebDriverException("Driver 已經被關閉。")

        # 1. 使用 WebDriverWait 尋找登出按鈕
        wait = WebDriverWait(driver, 10, poll_frequency=0.1)
        logout_button = wait.until(EC.element_to_be_clickable((By.XPATH, '//a[@title="登出"]')))
        
        # 2. 使用 JS 點擊，避免點擊後彈窗出現導致 Selenium 通訊卡死
        driver.execute_script("arguments[0].click();", logout_button)

        # 3. 處理彈窗
        # 如果你已經設定了 unhandledPromptBehavior="accept"，
        # 其實可以不用寫這段，但為了保險起見保留顯式處理
        try:
            wait.until(EC.alert_is_present())
            alert = driver.switch_to.alert
            alert.accept()
        except TimeoutException:
            # 如果設定了自動 accept，這裡抓不到彈窗是正常的
            pass

        return True

    except NoSuchElementException as e:
        logging.error(f"找不到 '登出' 按鈕: {e}")
        return False
    except TimeoutException as e:
        logging.error(f"登出處理超時: {e}")
        return False
    except UnexpectedAlertPresentException as e:
        logging.error(f"登出時遇到未預期彈窗: {e}")
        # 如果遇到了就嘗試 accept 它
        try: driver.switch_to.alert.accept()
        except: pass
        return False
    except Exception as e:
        logging.error(f"登出過程中發生未知錯誤: {e}")
        return False


async def logout(driver):
    return await asyncio.to_thread(_logout_sync, driver)


def crop_center(image_path, output_path, crop_width, crop_height):
    # 開啟截圖
    img = Image.open(image_path)

    # 獲取圖像尺寸
    img_width, img_height = img.size

    # 計算裁剪區域的左上角和右下角坐標 (以中心為基準)
    left = (img_width - crop_width) // 2
    top = (img_height - crop_height) // 2
    right = (img_width + crop_width) // 2
    bottom = (img_height + crop_height) // 2

    # 裁剪圖像
    cropped_img = img.crop((left, top, right, bottom))


    # 保存裁剪後的圖像
    cropped_img.save(output_path)

def _get_image_sync(driver, category):
    # 建立等待工具，最多等 15 秒
    wait = WebDriverWait(driver, 15)
    
    try:
        # 1. 強化 XPath (使用 contains 避開空格與全半形問題)
        # 我們找包含 category 名稱的 div，後面第一個包含 "QRCode" 字樣的按鈕
        qr_xpath = f'//div[contains(text(), "{category}")]//following::button[contains(text(), "QRCode")][1]'
        
        print(f"DEBUG: 正在等待 [{category}] 的 QR Code 按鈕出現...", flush=True)
        
        # 2. 使用 WebDriverWait 確保按鈕「已出現」且「可點擊」
        # 這會解決你遇到的 NoneType 問題
        qr_button = wait.until(EC.element_to_be_clickable((By.XPATH, qr_xpath)))
        
        # 3. 強制捲動並點擊 (使用 JavaScript 點擊以無視任何圖層遮擋)
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", qr_button)
        driver.execute_script("arguments[0].click();", qr_button)
        print(f"✅ 成功觸發 {category} 的 QR Code 彈窗")

        # 4. 等待 iframe 出現並切換 (取代 sleep(1))
        # wait.until 會在 iframe 一出現的瞬間就繼續，比 sleep(1) 快且穩
        wait.until(EC.presence_of_element_located((By.TAG_NAME, 'iframe')))
        driver.switch_to.frame(driver.find_element(By.TAG_NAME, 'iframe'))
        
        # 5. 設定路徑與存檔
        current_dir = os.path.dirname(os.path.abspath(__file__))
        parent_dir = os.path.dirname(current_dir)
        img_dir = os.path.join(parent_dir, 'img')
        os.makedirs(img_dir, exist_ok=True) # 確保資料夾存在

        image_path = os.path.join(img_dir, 'screenshot.png')
        image_path_crop = os.path.join(img_dir, 'screenshot_crop.png')

        # 6. 截圖與裁切
        driver.save_screenshot(image_path)
        crop_center(image_path, image_path_crop, 400, 700)
        
        print(f"✅ 截圖已儲存並裁切：{image_path_crop}")

        # 7. 清理狀態：切回主頁面並刷新，避免影響下次操作
        driver.switch_to.default_content()
        driver.refresh()

    except TimeoutException:
        print(f"❌ 錯誤：在 15 秒內找不到 {category} 的按鈕或 QR Code 視窗未彈出", flush=True)
        # 存下一張 debug 截圖看看當時發生了什麼
        driver.save_screenshot(os.path.join(parent_dir, 'log', 'debug_getimage_timeout.png'))
        raise Exception(f"無法在頁面上定位到 {category} 的 QR Code 按鈕")
    except Exception as e:
        print(f"❌ getImage 發生未知錯誤: {e!s}", flush=True)
        raise e


async def getImage(driver, category):
    await asyncio.to_thread(_get_image_sync, driver, category)


def get_ticket_num(driver, category):
    try:
        # 嘗試找到與 category 匹配的父 div 元素
        parent_div = driver.find_element(By.XPATH, f'//div[contains(@class, "TL") and contains(text(), "{category}")]/ancestor::div[@class="TI"]')
        
        # 嘗試找到包含 "可使用" 字樣的 span
        available_span = parent_div.find_element(By.XPATH, './/div[@class="CI"]/span[contains(text(), "可使用")]')
        available_text = available_span.text
        
        # 從文本中提取可用票數
        available_tickets = re.search(r'可使用 (\d+) 張', available_text).group(1)
        return available_tickets
    except NoSuchElementException as e:
        # 記錄找不到元素的錯誤
        logging.error(f"未找到與 {category} 匹配的票卷資訊: {e}")
        return None
    except AttributeError as e:
        # 記錄文本格式錯誤的錯誤
        logging.error(f"票數文本格式錯誤或不存在: {e}")
        return None

async def check_ticket_num(driver, ticket_num, category):
    """輪詢票數確認 QR Code 是否被使用。回傳 True=已使用、False=逾時未使用、None=查詢票數失敗"""
    counter = 0
    TIMEOUT_SECONDS = 5 * 60  # 5 分鐘
    start_time = time.time()
    while counter < 20 and (time.time() - start_time) < TIMEOUT_SECONDS:
        await asyncio.sleep(10)
        await asyncio.to_thread(driver.refresh)
        system_ticket_num = await asyncio.to_thread(get_ticket_num, driver, category)
        if system_ticket_num is None:
            return None
        elif int(system_ticket_num) == ticket_num - 1:
            return True
        counter += 1
        print(f"現在的 counter: {counter}")
    return False
