# -*- coding: utf-8 -*-
"""
BOSS直聘自动化求职工具
连接已有浏览器，免登录，JS直接提取岗位数据
"""

import json
import os
import time
import random
import re
import logging
import sys
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException,
    ElementClickInterceptedException, StaleElementReferenceException
)

DEBUG_PORT = 9222
SEL = {
    'search_url': 'https://www.zhipin.com/web/geek/job-recommend',
    'search_input': 'input[placeholder*="搜索"], input[class*="search"], [class*="search-form"] input',
    'job_cards': '.job-card-box, .job-card-wrap, .card-area',
    'apply_btn': '[class*="btn-startchat"], [class*="btn-chat"], div[class*="startchat"] a',
    'next_page': '.options-pages a:last-child, [class*="page-next"]',
    'privacy_agree': '[class*="policy"] [class*="agree"], [class*="privacy"] [class*="agree"]',
    'popup_input': 'textarea[placeholder*="消息"], [class*="chat-input"] textarea, [class*="edit-text"]',
    'popup_send': '[class*="btn-send"], [class*="send-btn"], button[class*="send"]',
    'popup_close_btn': '[class*="chat-dialog"] [class*="close"], [class*="im"] [class*="close"]',
    'chat_url': 'https://www.zhipin.com/web/geek/chat',
    'chat_items': '.chat-item, .dialog-item, .conversation-item',
    'chat_unread': '[class*="unread"], [class*="badge"], [class*="count"]',
    'chat_name': '[class*="user-name"], [class*="name"]',
    'chat_last_msg': '[class*="last-msg"], [class*="msg-preview"]',
    'msg_item': '[class*="message-item"], [class*="msg-item"], div[class*="msg"]',
    'msg_text': '[class*="msg-text"], [class*="text"], [class*="content"]',
    'detail_input': 'textarea[placeholder*="消息"], [class*="chat-input"] textarea, [class*="edit-area"] textarea',
    'detail_send': '[class*="btn-send"], [class*="send-btn"], button[class*="send"], span[class*="send"]',
    'captcha_slide': '[class*="geetest"], [class*="captcha"], [class*="verify"], [class*="slider"]',
}


# ==================== 日志 ====================
def _init_logger(name, filename, level=logging.INFO):
    os.makedirs('logs', exist_ok=True)
    lg = logging.getLogger(name)
    lg.setLevel(level)
    if lg.handlers:
        return lg
    fh = logging.FileHandler(f'logs/{filename}', encoding='utf-8')
    fh.setLevel(level)
    fh.setFormatter(logging.Formatter('%(asctime)s | %(levelname)s | %(message)s', datefmt='%m-%d %H:%M:%S'))
    lg.addHandler(fh)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(level)
    ch.setFormatter(logging.Formatter('[%(name)s] %(message)s'))
    lg.addHandler(ch)
    return lg


apply_log = _init_logger('apply', 'apply.log')
chat_log = _init_logger('chat', 'chat.log')
err_log = _init_logger('error', 'error.log', logging.WARNING)


# ==================== 配置 & 持久化 ====================
def load_config(path='config.json'):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_json(path, default=None):
    if default is None:
        default = {}
    if not os.path.exists(path):
        return default
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_records():
    return load_json('apply_record.json', {})


def save_records(rec):
    save_json('apply_record.json', rec)


def load_chat_log():
    return load_json('chat_history.json', {})


def save_chat_log(data):
    save_json('chat_history.json', data)


# ==================== 随机行为 ====================
def rand_delay(cfg, key_min, key_max):
    lo = cfg['anti_detect'].get(key_min, 2)
    hi = cfg['anti_detect'].get(key_max, 5)
    time.sleep(round(random.uniform(lo, hi), 2))


def type_like_human(el, text):
    cfg = load_config()
    t_min = cfg['anti_detect'].get('typing_speed_min', 0.08)
    t_max = cfg['anti_detect'].get('typing_speed_max', 0.35)
    el.click()
    time.sleep(random.uniform(0.3, 0.6))
    el.clear()
    for i, ch in enumerate(text):
        el.send_keys(ch)
        if ch in '，。,、！？；：':
            time.sleep(random.uniform(t_max * 2, t_max * 4))
        elif i > 0 and i % random.randint(5, 15) == 0:
            time.sleep(random.uniform(t_max * 2, t_max * 3))
        else:
            time.sleep(random.uniform(t_min, t_max))
    time.sleep(random.uniform(0.4, 0.8))


# ==================== 浏览器连接 ====================
def connect_to_browser():
    from selenium.webdriver.edge.options import Options as EdgeOptions
    apply_log.info(f'连接浏览器 127.0.0.1:{DEBUG_PORT} ...')
    opts = EdgeOptions()
    opts.add_experimental_option('debuggerAddress', f'127.0.0.1:{DEBUG_PORT}')
    try:
        driver = webdriver.Edge(options=opts)
    except Exception:
        apply_log.error(f'无法连接浏览器！请先运行 启动浏览器.bat 打开Edge')
        sys.exit(1)
    driver.set_page_load_timeout(30)
    return driver


# ==================== 弹窗 ====================
def handle_popups(driver):
    for t in ['同意', '我知道了', '知道了', '跳过', '暂不']:
        try:
            el = WebDriverWait(driver, 1).until(
                EC.element_to_be_clickable((By.XPATH, f"//*[contains(text(),'{t}')]")))
            el.click()
            time.sleep(random.uniform(0.3, 0.5))
        except Exception:
            continue


# ==================== 验证码 ====================
def check_captcha(driver):
    html = driver.page_source[:3000]
    if re.search(r'请[拖滑拉].*验证|滑块.*拼图|安全验证|请完成.*验证', html, re.I):
        if driver.find_elements(By.CSS_SELECTOR, SEL['captcha_slide']):
            apply_log.warning('检测到滑块验证码，请在浏览器中手动完成')
            time.sleep(3)
            return 'slide'
    if re.search(r'人脸识别|实名认证|扫脸', html):
        apply_log.warning('检测到人脸验证，请在浏览器中手动完成')
        time.sleep(3)
        return 'face'
    return None


# ==================== 投递引擎（JS提取） ====================
def _extract_jobs_js(driver):
    """用JS提取岗位数据 — 宽泛匹配所有可能的卡片容器"""
    js = '''
var selectors = [
    '.job-card-box', '.job-card-wrap', '.card-area',
    '.sub-ul-box li', '.recommend-job-list li',
    'li[class*="job"]', 'div[class*="job-card"]',
    '[class*="recommend"] li', '[class*="job-list"] li'
];
var cards = [];
for (var s of selectors) {
    try { var els = document.querySelectorAll(s); if (els.length > 3) { cards = els; break; } } catch(e) {}
}
if (!cards.length) {
    // 最后手段: 找所有包含薪资K特征的li
    var allLi = document.querySelectorAll('li');
    for (var i=0; i<allLi.length; i++) {
        if (/\\d+-\\d+K/.test(allLi[i].textContent) && allLi[i].textContent.length < 300) {
            cards.push(allLi[i]);
        }
    }
}
var results = [];
cards.forEach(function(card, idx) {
    var txt = card.textContent || '';
    // 尝试提取标题(第一个较长的文本子元素)
    var nameEl = card.querySelector('a[href*="job_detail"], [class*="name"], [class*="title"], span');
    var compEl = card.querySelector('[class*="company"], a[href*="company"]');
    var title = nameEl ? nameEl.textContent.trim() : txt.substring(0,40);
    var comp = compEl ? compEl.textContent.trim() : '';
    // 从文本中提取薪资
    var salaryMatch = txt.match(/(\\d+-\\d+K[^\\s]{0,10})/);
    var salary = salaryMatch ? salaryMatch[1] : '';
    // ID
    var link = card.querySelector('a[href*="job_detail"]');
    var jid = '';
    if (link) {
        var m = link.href.match(/job_detail[\\/]([a-zA-Z0-9_-]+)/);
        if (m) jid = m[1];
    }
    if (!jid) jid = card.getAttribute('data-jobid') || card.getAttribute('data-id') || '';
    if (!jid) jid = comp + '_' + title + '_' + idx;
    results.push({idx: idx, jid: jid, title: title, comp: comp, salary: salary});
});
return JSON.stringify(results);
'''
    try:
        raw = driver.execute_script(js)
        return json.loads(raw)
    except Exception as e:
        err_log.warning(f'JS提取失败: {e}')
        return []


def _blacklist_check(company, blacklist):
    for kw in blacklist:
        if kw in company:
            return True
    return False


def search_jobs(driver, keyword, city_code, max_pages, blacklist, record):
    found = []
    # 导航到推荐页，用搜索框搜（比URL可靠）
    driver.get(SEL['search_url'])
    time.sleep(random.uniform(3, 5))
    handle_popups(driver)

    # 找搜索框输入关键词
    search_inp = None
    for sel in SEL['search_input'].split(', '):
        try:
            search_inp = WebDriverWait(driver, 3).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, sel)))
            break
        except Exception:
            continue

    if search_inp:
        try:
            search_inp.click()
            time.sleep(random.uniform(0.3, 0.6))
            search_inp.clear()
            time.sleep(random.uniform(0.2, 0.4))
            for ch in keyword:
                search_inp.send_keys(ch)
                time.sleep(random.uniform(0.05, 0.15))
            time.sleep(random.uniform(0.5, 1.0))
            search_inp.send_keys(Keys.ENTER)
        except Exception:
            # fallback: URL导航
            driver.get(f'https://www.zhipin.com/web/geek/jobs?query={keyword}&city={city_code}')
    else:
        driver.get(f'https://www.zhipin.com/web/geek/jobs?query={keyword}&city={city_code}')

    time.sleep(random.uniform(4, 6))
    handle_popups(driver)

    for _ in range(8):
        driver.execute_script(f'window.scrollBy(0, {random.randint(400, 800)})')
        time.sleep(random.uniform(0.5, 1.0))

    for page in range(1, max_pages + 1):
        apply_log.info(f'搜索 [{keyword}] 第{page}页')
        time.sleep(random.uniform(1, 2))

        jobs = _extract_jobs_js(driver)
        if not jobs:
            time.sleep(3)
            for _ in range(5):
                driver.execute_script(f'window.scrollBy(0, {random.randint(400, 700)})')
                time.sleep(random.uniform(0.5, 1))
            jobs = _extract_jobs_js(driver)

        if not jobs:
            cur_url = driver.current_url.lower()
            if 'login' in cur_url or 'passport' in cur_url:
                apply_log.error('登录页重定向！需要重新登录')
                return found
            body_text = driver.find_element(By.TAG_NAME, 'body').text[:1000]
            if '没有找到' in body_text or '暂无' in body_text:
                apply_log.info(f'搜索 [{keyword}] 无结果')
                break
            apply_log.warning(f'搜索 [{keyword}] 第{page}页 未找到岗位卡片')
            continue

        apply_log.info(f'  找到 {len(jobs)} 个岗位')
        # 取Selenium元素用于后续点击
        card_els = driver.find_elements(By.CSS_SELECTOR, SEL['job_cards'])
        for item in jobs:
            jid = item['jid']
            comp = item['comp']
            title = item['title']
            if not jid or jid in record:
                continue
            if _blacklist_check(comp, blacklist):
                apply_log.info(f'黑名单跳过: {comp}')
                continue
            el = card_els[item['idx']] if item['idx'] < len(card_els) else None
            found.append((el, jid, comp, title))

        # 翻页
        if page < max_pages:
            next_btn = None
            for sel in SEL['next_page'].split(', '):
                try:
                    next_btn = driver.find_element(By.CSS_SELECTOR, sel)
                    break
                except Exception:
                    continue
            if next_btn:
                driver.execute_script('arguments[0].click()', next_btn)
                time.sleep(random.uniform(2, 4))
                handle_popups(driver)
                for _ in range(random.randint(4, 6)):
                    driver.execute_script(f'window.scrollBy(0, {random.randint(300, 600)})')
                    time.sleep(random.uniform(0.4, 0.8))
            else:
                apply_log.info(f'搜索 [{keyword}] 无下一页，共{page}页')
                break
    return found


def apply_single(driver, card_el, keyword, cfg, record):
    """对当前卡片元素立即投递 — 点卡片→找沟通按钮→发消息→记录"""
    # 从卡片提取信息
    txt = card_el.text.strip()
    comp = ''
    title = ''
    jid = ''
    try:
        link = card_el.find_element(By.CSS_SELECTOR, 'a[href*="job_detail"]')
        href = link.get_attribute('href')
        m = re.search(r'job_detail/([a-zA-Z0-9_-]+)', href)
        if m:
            jid = m.group(1)
        title = link.text.strip() or txt[:40]
    except Exception:
        pass
    if not jid:
        jid = card_el.get_attribute('data-jobid') or card_el.get_attribute('data-id') or ''
    if not jid:
        jid = f'{comp}_{title}_{int(time.time())}'

    apply_log.info(f'  投递: {txt[:60]}')

    try:
        driver.execute_script('arguments[0].scrollIntoView({block:"center"})', card_el)
        time.sleep(random.uniform(0.5, 1.0))

        # 点卡片进详情
        link = None
        try:
            link = card_el.find_element(By.CSS_SELECTOR, 'a[href*="job_detail"]')
        except Exception:
            try:
                link = card_el.find_element(By.TAG_NAME, 'a')
            except Exception:
                pass
        if link:
            driver.execute_script('arguments[0].click()', link)
        else:
            driver.execute_script('arguments[0].click()', card_el)

        time.sleep(random.uniform(2.5, 4))
        handle_popups(driver)

        # XPath找沟通按钮
        btn = None
        for xp in [
            '//*[text()="立即沟通"]',
            '//span[text()="立即沟通"]',
            '//a[text()="立即沟通"]',
            '//*[contains(text(),"立即沟通")]',
            '//span[contains(text(),"沟通")]',
            '//a[contains(text(),"沟通")]',
            '//*[contains(@class,"btn-startchat")]',
            '//*[contains(@class,"btn-chat")]',
        ]:
            try:
                btns = driver.find_elements(By.XPATH, xp)
                if btns:
                    btn = btns[0]
                    break
            except Exception:
                continue

        if not btn:
            record[jid] = {'time': datetime.now().isoformat(), 'company': comp,
                           'title': title, 'status': 'no_btn', 'card_text': txt[:80]}
            apply_log.info(f'    未找到沟通按钮')
            return False

        if btn.text.strip() in ('已沟通', '已投递', '已申请', '继续沟通'):
            record[jid] = {'time': datetime.now().isoformat(), 'company': comp,
                           'title': title, 'status': 'already'}
            return False

        driver.execute_script('arguments[0].click()', btn)

        intro = cfg.get('self_intro', '您好，对这个岗位很感兴趣，方便聊聊吗？')
        try:
            inp = WebDriverWait(driver, 6).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, SEL['popup_input'])))
            type_like_human(inp, intro)
            time.sleep(random.uniform(0.6, 1.0))
            send_btn = driver.find_element(By.CSS_SELECTOR, SEL['popup_send'])
            send_btn.click()
            time.sleep(random.uniform(1.0, 2.0))
            record[jid] = {'time': datetime.now().isoformat(), 'company': comp,
                           'title': title, 'status': 'sent'}
            apply_log.info(f'投递成功: {comp} - {title}')
        except TimeoutException:
            record[jid] = {'time': datetime.now().isoformat(), 'company': comp,
                           'title': title, 'status': 'unknown'}
            apply_log.warning(f'聊天窗未弹出: {comp} - {title}')

        try:
            close_btn = driver.find_element(By.CSS_SELECTOR, SEL['popup_close_btn'])
            close_btn.click()
            time.sleep(random.uniform(0.3, 0.8))
        except Exception:
            pass
        return True

    except StaleElementReferenceException:
        record[jid] = {'time': datetime.now().isoformat(), 'company': comp,
                       'title': title, 'status': 'stale'}
        return False
    except Exception as e:
        err_log.error(f'投递异常 {comp} - {title}: {e}')
        record[jid] = {'time': datetime.now().isoformat(), 'company': comp,
                       'title': title, 'status': 'error'}
        return False


def job_apply_loop(driver, cfg, record):
    filters = cfg['job_filters']
    keywords = filters.get('keywords', [])
    city = filters.get('city_code', '101280600')
    max_pages = filters.get('max_pages', 5)
    blacklist = filters.get('blacklist_companies', [])
    hourly = filters.get('hourly_limit', 10)
    daily = filters.get('daily_limit', 50)

    today = datetime.now().strftime('%Y%m%d')
    today_count = sum(1 for v in record.values() if v.get('time', '').startswith(today))
    this_hour = datetime.now().strftime('%Y%m%d-%H')
    hour_count = sum(1 for v in record.values() if v.get('time', '').startswith(this_hour))

    if today_count >= daily:
        apply_log.info(f'今日限额{daily}已满')
        return 0
    if hour_count >= hourly:
        apply_log.info(f'本小时限额{hourly}已满')
        return 0

    applied = 0
    quota = min(hourly - hour_count, daily - today_count)
    random.shuffle(keywords)

    for kw in keywords:
        if applied >= quota:
            break
        apply_log.info(f'=== 关键词: {kw} ===')
        driver.get(SEL['search_url'])
        time.sleep(random.uniform(3, 5))
        handle_popups(driver)

        # 用搜索框搜
        try:
            search_inp = WebDriverWait(driver, 3).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, 'input[placeholder*="搜索"]')))
            search_inp.click(); time.sleep(random.uniform(0.2, 0.4))
            search_inp.clear(); time.sleep(random.uniform(0.1, 0.2))
            for ch in kw:
                search_inp.send_keys(ch); time.sleep(random.uniform(0.03, 0.1))
            time.sleep(random.uniform(0.4, 0.8))
            search_inp.send_keys(Keys.ENTER)
        except Exception:
            driver.get(f'https://www.zhipin.com/web/geek/jobs?query={kw}&city={city}')

        time.sleep(random.uniform(4, 6))
        handle_popups(driver)
        for _ in range(8):
            driver.execute_script(f'window.scrollBy(0, {random.randint(400, 800)})')
            time.sleep(random.uniform(0.5, 1.0))

        for page in range(1, max_pages + 1):
            if applied >= quota:
                break
            apply_log.info(f'  [{kw}] 第{page}页')

            # 取当前页卡片
            card_els = _find_elements(driver, 'job_cards')
            if not card_els:
                time.sleep(2)
                for _ in range(5):
                    driver.execute_script(f'window.scrollBy(0, {random.randint(400, 700)})')
                    time.sleep(random.uniform(0.5, 1))
                card_els = _find_elements(driver, 'job_cards')

            if not card_els:
                apply_log.warning(f'  [{kw}] 第{page}页 无卡片')
                body = driver.find_element(By.TAG_NAME, 'body').text[:500]
                if '没有找到' in body or '暂无' in body:
                    break
                continue

            apply_log.info(f'    找到 {len(card_els)} 个卡片')

            # 逐个处理当前页卡片（立即投递，不跨页保存元素）
            for card_el in card_els:
                if applied >= quota:
                    break
                try:
                    handle_popups(driver)
                    apply_single(driver, card_el, kw, cfg, record)
                    applied += 1
                    save_records(record)
                    rand_delay(cfg, 'apply_delay_min', 'apply_delay_max')
                    # 投递完回到搜索结果页
                    driver.back()
                    time.sleep(random.uniform(2, 3))
                    handle_popups(driver)
                    for _ in range(4):
                        driver.execute_script(f'window.scrollBy(0, {random.randint(300, 600)})')
                        time.sleep(random.uniform(0.4, 0.7))
                except StaleElementReferenceException:
                    continue
                except Exception as e:
                    err_log.error(f'投递异常: {e}')
                    continue

            # 翻页
            if page < max_pages and applied < quota:
                try:
                    next_btn = None
                    for sel in SEL['next_page'].split(', '):
                        try:
                            next_btn = driver.find_element(By.CSS_SELECTOR, sel)
                            break
                        except Exception:
                            continue
                    if next_btn:
                        driver.execute_script('arguments[0].click()', next_btn)
                        time.sleep(random.uniform(2, 4))
                        handle_popups(driver)
                        for _ in range(random.randint(4, 6)):
                            driver.execute_script(f'window.scrollBy(0, {random.randint(300, 600)})')
                            time.sleep(random.uniform(0.4, 0.8))
                    else:
                        break
                except Exception:
                    break

    apply_log.info(f'本轮投递 {applied} 个')
    return applied


# ==================== 聊天引擎 ====================
def detect_intent(msg_text):
    text = msg_text.strip()
    if re.search(r'面试|面聊|面谈|过来聊聊|到公司|线下面|约个时间', text):
        return 'interview_invite'
    if re.search(r'简历|附件|发.*简历|简历.*看', text):
        return 'ask_resume'
    if re.search(r'薪资|工资|待遇|薪酬|期望.*薪|多少钱', text):
        return 'ask_salary'
    if re.search(r'经验|几年|做过|技术栈|会什么|用什么', text):
        return 'ask_experience'
    if re.search(r'在职|离职|还在吗|到岗|入职|什么时候.*到', text):
        return 'ask_current_status'
    return 'greeting'


def pick_reply(intent, cfg):
    templates = cfg.get('reply_templates', {}).get(intent, [])
    if not templates:
        templates = cfg.get('reply_templates', {}).get('greeting', ['你好，岗位还在招吗？'])
    return random.choice(templates)


def send_reply(driver, text):
    try:
        inp = WebDriverWait(driver, 4).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, SEL['detail_input'])))
        type_like_human(inp, text)
        time.sleep(random.uniform(0.4, 0.8))
        send_btn = driver.find_element(By.CSS_SELECTOR, SEL['detail_send'])
        send_btn.click()
        chat_log.info(f'已发送: {text[:30]}...')
        return True
    except Exception as e:
        err_log.error(f'发送失败: {e}')
        return False


def poll_and_reply(driver, cfg):
    chat_records = load_chat_log()
    replied = 0

    for _ in range(8):
        driver.get(SEL['chat_url'])
        time.sleep(random.uniform(2.5, 4))
        handle_popups(driver)
        for _ in range(random.randint(2, 3)):
            driver.execute_script(f'window.scrollBy(0, {random.randint(200, 500)})')
            time.sleep(random.uniform(0.5, 1))

        items = driver.find_elements(By.CSS_SELECTOR, SEL['chat_items'])
        if not items:
            break

        target = None
        target_name = ''
        target_preview = ''
        for idx, item in enumerate(items):
            try:
                unread = False
                try:
                    badge = item.find_element(By.CSS_SELECTOR, SEL['chat_unread'])
                    if badge.text.strip():
                        unread = True
                except NoSuchElementException:
                    pass
                if not unread:
                    continue
                try:
                    target_name = item.find_element(By.CSS_SELECTOR, SEL['chat_name']).text.strip()
                except Exception:
                    target_name = '未知'
                try:
                    target_preview = item.find_element(By.CSS_SELECTOR, SEL['chat_last_msg']).text.strip()
                except Exception:
                    target_preview = ''
                if not target_preview:
                    continue
                target = item
                break
            except StaleElementReferenceException:
                continue

        if target is None:
            break

        chat_log.info(f'处理会话: {target_name} - {target_preview[:30]}')
        try:
            driver.execute_script('arguments[0].click()', target)
        except StaleElementReferenceException:
            continue
        time.sleep(random.uniform(1.5, 2.5))

        hr_msg = target_preview
        try:
            msgs = driver.find_elements(By.CSS_SELECTOR, SEL['msg_item'])
            for m in reversed(msgs):
                cls = m.get_attribute('class') or ''
                if 'self' not in cls and 'send' not in cls and 'right' not in cls:
                    try:
                        hr_msg = m.find_element(By.CSS_SELECTOR, SEL['msg_text']).text.strip()
                    except Exception:
                        pass
                    break
        except Exception:
            pass

        boss_key = target_name or f'boss_{idx}'
        last_replied = chat_records.get(boss_key, {}).get('last_replied_msg', '') if boss_key in chat_records else ''
        if hr_msg == last_replied:
            chat_log.info(f'已回复过，跳过: {target_name}')
            continue

        intent = detect_intent(hr_msg)
        reply = pick_reply(intent, cfg)
        send_reply(driver, reply)
        replied += 1

        chat_records[boss_key] = {
            'name': target_name, 'last_msg': hr_msg, 'last_replied_msg': hr_msg,
            'replied_at': datetime.now().isoformat(), 'intent': intent, 'reply': reply}
        save_chat_log(chat_records)
        rand_delay(cfg, 'msg_delay_min', 'msg_delay_max')

    if replied > 0:
        chat_log.info(f'本轮自动回复 {replied} 条')
    return replied


# ==================== 主循环 ====================
def main():
    cfg = load_config()
    os.makedirs(cfg.get('paths', {}).get('log_dir', 'logs'), exist_ok=True)

    apply_log.info('==== BOSS直聘自动化 ====')
    apply_log.info(f'关键词: {cfg["job_filters"]["keywords"]}')
    apply_log.info(f'黑名单: {cfg["job_filters"]["blacklist_companies"]}')

    driver = connect_to_browser()

    # 切到BOSS
    apply_log.info('导航到BOSS直聘...')
    driver.get('https://www.zhipin.com/web/geek/job-recommend')
    time.sleep(random.uniform(3, 5))
    handle_popups(driver)

    # 检查登录
    body = driver.find_element(By.TAG_NAME, 'body').text[:500]
    if '登录' in body and ('扫码' in body or '验证码' in body or '手机号' in body):
        apply_log.info('=== 请在浏览器中登录 BOSS直聘 ===')
        apply_log.info('=== 用手机验证码或扫码登录，登录后程序自动继续 ===')
        for i in range(120):
            time.sleep(5)
            try:
                body = driver.find_element(By.TAG_NAME, 'body').text[:500]
                if '登录' not in body or ('扫码' not in body and '验证码' not in body and '手机号' not in body):
                    apply_log.info('登录成功！')
                    break
            except Exception:
                pass
            if i % 12 == 0:
                apply_log.info(f'等待登录... ({(i+1)*5}s)')
        else:
            apply_log.error('登录超时，请登录后重启程序')
            sys.exit(1)
    else:
        apply_log.info('登录状态: OK')

    record = load_records()
    apply_log.info(f'已加载投递记录 {len(record)} 条')

    cycle = 0
    try:
        while True:
            cycle += 1
            now = datetime.now()

            long_rest_hour = cfg['anti_detect'].get('long_rest_hour', 0)
            long_rest_dur = cfg['anti_detect'].get('long_rest_duration_min', 0)
            if long_rest_hour and long_rest_dur and now.hour == long_rest_hour:
                apply_log.info(f'午休 {long_rest_dur} 分钟...')
                time.sleep(long_rest_dur * 60)

            captcha = check_captcha(driver)
            if captcha:
                apply_log.warning(f'验证码类型: {captcha}，请在浏览器中手动完成')
                time.sleep(5)
                continue

            record = load_records()

            try:
                job_apply_loop(driver, cfg, record)
            except Exception as e:
                err_log.error(f'投递循环异常: {e}')

            try:
                poll_and_reply(driver, cfg)
            except Exception as e:
                err_log.error(f'聊天监控异常: {e}')

            rest_dur = cfg['anti_detect'].get('rest_duration_min', 15)
            apply_log.info(f'第{cycle}轮完成，休息 {rest_dur} 分钟...')
            time.sleep(rest_dur * 60 + random.uniform(0, 120))

    except KeyboardInterrupt:
        apply_log.info('用户终止')
    except Exception as e:
        err_log.error(f'主程序异常: {e}', exc_info=True)
    finally:
        apply_log.info('==== 程序退出 ====')


if __name__ == '__main__':
    main()
