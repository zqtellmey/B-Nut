import time
import os
import json
import re
import requests
import platform
from datetime import datetime

# 虚拟显示器处理（针对无头 Linux 环境）
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
# 代理逻辑：优先读取 GitHub Actions 的 sing-box 变量
RAW_PROXY = os.getenv("PROXY_SOCKS5") or os.getenv("PROXY") or ""

def format_proxy(p):
    if not p: return None
    # 强制使用 socks5h 以支持远程 DNS 解析，这对绕过某些限制至关重要
    if p.startswith("socks5://"):
        return p.replace("socks5://", "socks5h://")
    if "://" not in p:
        return f"socks5h://{p}"
    return p

PROXY = format_proxy(RAW_PROXY)

TG_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")
ACCOUNTS = os.getenv("BYTENUT", "")

URL_LOGIN_PANEL = "https://www.bytenut.com/auth/login"
URL_HOMEPAGE = "https://www.bytenut.com/homepage"
API_SERVER_LIST = "https://www.bytenut.com/game-panel/api/gpPanelServer/user/servers"
API_EXTENSION_INFO = "https://www.bytenut.com/game-panel/api/gp-free-server/extension-info/{}"
API_START_SERVER = "https://www.bytenut.com/game-panel/api/serverStartQueue/requestStart/{}"

RENEW_MENU = '//li[contains(., "RENEW SERVER")]'
EXTEND_BTN = "button.extend-btn"

def parse_accounts(raw: str):
    accounts = []
    for line in raw.strip().split('\n'):
        line = line.strip()
        if not line or '-----' not in line:
            continue
        parts = line.split('-----', 1)
        if len(parts) == 2:
            accounts.append((parts[0].strip(), parts[1].strip()))
    return accounts

class BytenutRenewal:
    def __init__(self):
        self.BASE_DIR = os.path.dirname(os.path.abspath(__file__))
        self.screenshot_dir = os.path.join(self.BASE_DIR, "artifacts")
        os.makedirs(self.screenshot_dir, exist_ok=True)
        
        # 初始化带代理的 Session 用于所有 API 和 TG 通知
        self.session = requests.Session()
        if PROXY:
            print(f"[{time.strftime('%H:%M:%S')}] [PROXY] 全局代理已设为: {PROXY}")
            # requests 不支持 socks5h 前缀，需转回 socks5
            req_proxy = PROXY.replace("socks5h://", "socks5://")
            self.session.proxies = {"http": req_proxy, "https": req_proxy}

    def mask_account(self, u):
        if not u: return "Unknown"
        u = u.strip()
        if "@" in u:
            local, domain = u.split("@", 1)
            local = local[:2] + "*" * (len(local) - 2) if len(local) > 2 else local[0] + "*"
            return f"{local}@{domain}"
        return u[:2] + "*" * (len(u) - 2) if len(u) > 2 else u[0] + "*"

    def mask_server_id(self, sid):
        if not sid: return "****"
        return "****" + sid[-4:] if len(sid) > 4 else "****"

    def log(self, msg):
        print(f"[{time.strftime('%H:%M:%S')}] [INFO] {msg}", flush=True)

    def shot(self, sb, name):
        path = os.path.join(self.screenshot_dir, name)
        sb.save_screenshot(path)
        return path

    def send_tg(self, icon, title, account_name, server_id, state_str, expiry_str, extra="", screenshot=None):
        if not TG_TOKEN or not TG_CHAT_ID: return
        msg = f"{icon} {title}\n\n账号: {account_name}\n服务器: {server_id}\n状态: {state_str}\n到期: {expiry_str}\n"
        if extra: msg += f"\n{extra}\n"
        msg += "\nByteNut Auto Renew"
        try:
            if screenshot and os.path.exists(screenshot):
                url = f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto"
                self.session.post(url, data={"chat_id": TG_CHAT_ID, "caption": msg}, files={"photo": open(screenshot, "rb")}, timeout=20)
            else:
                url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
                self.session.post(url, data={"chat_id": TG_CHAT_ID, "text": msg}, timeout=20)
        except Exception as e:
            self.log(f"TG发送失败: {e}")

    def get_full_cookies(self, sb):
        try:
            result = sb.driver.execute_cdp_cmd('Network.getCookies', {})
            return {c['name']: c['value'] for c in result.get('cookies', [])}
        except:
            return {c['name']: c['value'] for c in sb.get_cookies()}

    def get_yl_token(self, sb):
        return sb.execute_script("return localStorage.getItem('yl-token') || sessionStorage.getItem('yl-token') || '';")

    def call_api(self, sb, url, method="GET", referer=URL_HOMEPAGE):
        headers = {
            "User-Agent": sb.execute_script("return navigator.userAgent;"),
            "Accept": "application/json, text/plain, */*",
            "Referer": referer,
            "Yl-Token": self.get_yl_token(sb)
        }
        try:
            if method == "GET":
                resp = self.session.get(url, headers=headers, cookies=self.get_full_cookies(sb), timeout=15)
            else:
                headers["Content-Type"] = "application/x-form-urlencoded"
                resp = self.session.post(url, headers=headers, cookies=self.get_full_cookies(sb), timeout=15)
            
            res_json = resp.json()
            return res_json.get('data') if res_json.get('code') == 200 else None
        except Exception as e:
            self.log(f"API {method} 失败: {e}")
            return None

    def remove_overlay_ads(self, sb):
        sb.execute_script("""
            (function() {
                var btn = document.getElementById('ez-accept-all');
                if (btn) btn.click();
                document.querySelectorAll('ins.adsbygoogle, iframe[id^="aswift"], .ezoic-floating-bottom').forEach(el => el.remove());
                document.body.style.overflow = 'auto';
            })();
        """)

    def wait_turnstile(self, sb, timeout=60):
        self.log("⏳ 正在处理 Turnstile 验证...")
        start = time.time()
        while time.time() - start < timeout:
            self.remove_overlay_ads(sb)
            try:
                # 尝试自动寻找并点击（SeleniumBase UC 特色功能）
                sb.uc_gui_click_captcha()
                val = sb.execute_script('return document.querySelector("[name=cf-turnstile-response]")?.value')
                if val and len(val) > 20:
                    self.log("✅ Turnstile 验证通过")
                    return True
            except: pass
            time.sleep(2)
        return False

    def try_extend(self, sb, server_id, old_expiry):
        if not self.wait_turnstile(sb): return False, ""
        self.remove_overlay_ads(sb)
        try:
            if sb.is_element_visible(EXTEND_BTN):
                sb.click(EXTEND_BTN)
                time.sleep(3)
                # 处理弹窗
                sb.execute_script("var b = document.querySelector('button.reward-option--watch'); if(b) b.click();")
                time.sleep(5)
                # 模拟观看后的 Claim
                sb.execute_script("var b = document.querySelector('div.adsterra-rewarded-dialog button.el-button--success'); if(b) b.click();")
                time.sleep(5)
                
                # 检查结果
                new_data = self.call_api(sb, API_EXTENSION_INFO.format(server_id))
                new_expiry = new_data.get("expiredTime") if new_data else ""
                if new_expiry and new_expiry != old_expiry:
                    return True, new_expiry
        except Exception as e:
            self.log(f"续期点击异常: {e}")
        return False, ""

    def run(self):
        accounts = parse_accounts(ACCOUNTS)
        if not accounts: return

        for idx, (user, pwd) in enumerate(accounts, 1):
            self.log(f"开始处理账号: {self.mask_account(user)}")
            
            # 使用更严谨的代理注入方式
            with SB(uc=True, test=True, headed=True, proxy=PROXY) as sb:
                try:
                    # 验证代理 IP（调试用，可在日志查看出口 IP）
                    try:
                        curr_ip = sb.execute_script("return fetch('https://api.ipify.org').then(r => r.text())")
                        self.log(f"当前浏览器出口 IP: {curr_ip}")
                    except: pass

                    sb.uc_open_with_reconnect(URL_LOGIN_PANEL, 5)
                    sb.type('input[placeholder="Username"]', user)
                    sb.type('input[placeholder="Password"]', pwd)
                    sb.click('//button[contains(., "Sign In")]')
                    time.sleep(5)

                    if "/auth/login" in sb.get_current_url():
                        self.log("❌ 登录失败")
                        continue

                    # 获取服务器状态
                    servers = self.call_api(sb, API_SERVER_LIST)
                    if not servers: continue
                    
                    srv = servers[0]
                    sid = srv['id']
                    old_exp = srv.get('expiredTime', '')
                    
                    # 进入详情页
                    sb.uc_open_with_reconnect(f"https://www.bytenut.com/free-gamepanel/{sid}", 5)
                    time.sleep(3)
                    sb.click(RENEW_MENU)
                    time.sleep(2)

                    success, new_exp = self.try_extend(sb, sid, old_exp)
                    if success:
                        self.send_tg("✅", "续期成功", user, sid, "Running", new_exp, screenshot=self.shot(sb, f"success_{idx}.png"))
                    else:
                        self.send_tg("⚠️", "未续期", user, sid, "Unknown", old_exp, "可能还在冷却或验证失败")

                except Exception as e:
                    self.log(f"流程中断: {e}")

if __name__ == "__main__":
    BytenutRenewal().run()
