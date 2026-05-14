import time
import os
import requests
import platform
from datetime import datetime

# 虚拟显示器处理（针对 GitHub Actions 运行环境）
if "DISPLAY" not in os.environ:
    if platform.system().lower() == "linux":
        try:
            from pyvirtualdisplay import Display
            display = Display(visible=False, size=(1920, 1080))
            display.start()
            os.environ["DISPLAY"] = display.new_display_var
        except:
            pass

from seleniumbase import SB

# ================= 配置区域 =================
# 删除了 PROXY 变量和 format_proxy 函数，完全透传系统网络
TG_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")
ACCOUNTS = os.getenv("BYTENUT", "")

URL_LOGIN_PANEL = "https://www.bytenut.com/auth/login"
URL_HOMEPAGE = "https://www.bytenut.com/homepage"
API_SERVER_LIST = "https://www.bytenut.com/game-panel/api/gpPanelServer/user/servers"
API_EXTENSION_INFO = "https://www.bytenut.com/game-panel/api/gp-free-server/extension-info/{}"

RENEW_MENU = '//li[contains(., "RENEW SERVER")]'
EXTEND_BTN = "button.extend-btn"

def parse_accounts(raw: str):
    accounts = []
    if not raw: return accounts
    for line in raw.strip().split('\n'):
        line = line.strip()
        if '-----' in line:
            parts = line.split('-----', 1)
            accounts.append((parts[0].strip(), parts[1].strip()))
    return accounts

class BytenutRenewal:
    def __init__(self):
        self.BASE_DIR = os.path.dirname(os.path.abspath(__file__))
        self.screenshot_dir = os.path.join(self.BASE_DIR, "artifacts")
        os.makedirs(self.screenshot_dir, exist_ok=True)
        # Requests 此时会自动继承系统环境变量或直接走全局网卡，无需设置 proxies
        self.session = requests.Session()

    def log(self, msg):
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

    def shot(self, sb, name):
        path = os.path.join(self.screenshot_dir, name)
        sb.save_screenshot(path)
        return path

    def send_tg(self, icon, title, account, server_id, state, expiry, extra="", screenshot=None):
        if not TG_TOKEN or not TG_CHAT_ID: return
        msg = f"{icon} {title}\n\n账号: {account}\nID: {server_id}\n状态: {state}\n到期: {expiry}\n"
        if extra: msg += f"\n提示: {extra}\n"
        try:
            if screenshot:
                url = f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto"
                self.session.post(url, data={"chat_id": TG_CHAT_ID, "caption": msg}, files={"photo": open(screenshot, "rb")}, timeout=20)
            else:
                url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
                self.session.post(url, data={"chat_id": TG_CHAT_ID, "text": msg}, timeout=20)
        except Exception as e:
            self.log(f"TG通知发送失败: {e}")

    def call_api(self, sb, url):
        yl_token = sb.execute_script("return localStorage.getItem('yl-token') || '';")
        headers = {
            "User-Agent": sb.execute_script("return navigator.userAgent;"),
            "Yl-Token": yl_token,
            "Referer": URL_HOMEPAGE
        }
        try:
            for cookie in sb.get_cookies():
                self.session.cookies.set(cookie['name'], cookie['value'])
            # 这里不加 proxy 参数，直接发请求
            resp = self.session.get(url, headers=headers, timeout=15)
            return resp.json().get('data') if resp.status_code == 200 else None
        except:
            return None

    def handle_turnstile(self, sb):
        self.log("⏳ 等待检测 Turnstile 验证...")
        for _ in range(30):
            sb.execute_script("document.querySelectorAll('.ezoic-floating-bottom, #ez-accept-all').forEach(el => el.remove());")
            try:
                sb.uc_gui_click_captcha()
                res = sb.execute_script('return document.querySelector("[name=cf-turnstile-response]")?.value')
                if res and len(res) > 20:
                    self.log("✅ 验证已通过")
                    return True
            except: pass
            time.sleep(2)
        return False

    def run(self):
        accounts = parse_accounts(ACCOUNTS)
        if not accounts: return

        for idx, (user, pwd) in enumerate(accounts, 1):
            self.log(f"--- 账号 [{idx}]: {user[:3]}*** ---")
            
            # 关键修改：取消 proxy 参数，让浏览器直接走系统网卡
            with SB(uc=True, test=True, headed=True) as sb:
                try:
                    # 验证网络出口
                    try:
                        sb.open("https://www.cloudflare.com/cdn-cgi/trace")
                        trace_text = sb.get_text("body")
                        ip_line = [l for l in trace_text.split('\n') if l.startswith('ip=')]
                        self.log(f"📡 系统当前出口 IP: {ip_line[0] if ip_line else 'Unknown'}")
                        if "warp=on" in trace_text:
                            self.log("✅ WARP 全局状态确认为: ON")
                    except:
                        pass

                    sb.uc_open_with_reconnect(URL_LOGIN_PANEL, 5)
                    sb.type('input[placeholder="Username"]', user)
                    sb.type('input[placeholder="Password"]', pwd)
                    sb.click('//button[contains(., "Sign In")]')
                    time.sleep(5)

                    if "/auth/login" in sb.get_current_url():
                        self.log("❌ 登录失败")
                        continue

                    servers = self.call_api(sb, API_SERVER_LIST)
                    if not servers: continue
                    
                    sid = servers[0]['id']
                    old_exp = servers[0].get('expiredTime', 'Unknown')
                    
                    sb.uc_open_with_reconnect(f"https://www.bytenut.com/free-gamepanel/{sid}", 5)
                    time.sleep(3)
                    sb.click(RENEW_MENU)
                    time.sleep(2)

                    if self.handle_turnstile(sb):
                        if sb.is_element_visible(EXTEND_BTN):
                            sb.click(EXTEND_BTN)
                            time.sleep(5)
                            sb.execute_script("var b = document.querySelector('button.reward-option--watch'); if(b) b.click();")
                            time.sleep(15) 
                            sb.execute_script("var b = document.querySelector('button.el-button--success'); if(b) b.click();")
                            time.sleep(5)
                            
                            new_info = self.call_api(sb, API_EXTENSION_INFO.format(sid))
                            new_exp = new_info.get('expiredTime', old_exp) if new_info else old_exp
                            
                            if new_exp != old_exp:
                                self.log(f"🎉 续期成功: {new_exp}")
                                self.send_tg("✅", "续期成功", user, sid, "Running", new_exp, screenshot=self.shot(sb, f"ok_{idx}.png"))
                            else:
                                self.send_tg("⏳", "未更新", user, sid, "Running", old_exp)
                    else:
                        self.send_tg("❌", "验证超时", user, sid, "Error", old_exp)

                except Exception as e:
                    self.log(f"💥 异常: {str(e)}")

if __name__ == "__main__":
    BytenutRenewal().run()
