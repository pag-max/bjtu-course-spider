# -*- coding: utf-8 -*-
"""
北交大课程平台课件爬虫（带图形界面）
====================================
流程：统一身份认证(SSO/OAuth) 登录 -> 拉取课程列表 -> 选目录加载文件 -> 勾选批量下载
下载原理：预览页 iframe 直链(kk 服务器免登录) + 官方下载接口兜底

依赖：pip install requests
"""

import base64
import html
import os
import re
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

import requests

SSO_BASE = "https://sso.bjtu.edu.cn"
SSO_LOGIN = SSO_BASE + "/sso/login"
MIS_HOME = "https://mis.bjtu.edu.cn/home/"
MIS_APP_ENTRY = "https://mis.bjtu.edu.cn/module/module/28/"
PLATFORM = "http://123.121.147.7:88"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "账号配置.txt")


def load_config():
    """从 账号配置.txt 读取账号(第1行)和密码(第2行)"""
    if not os.path.exists(CONFIG_FILE):
        return "", ""
    with open(CONFIG_FILE, "r", encoding="utf-8-sig") as f:
        lines = [ln.strip() for ln in f if ln.strip()]
    if len(lines) < 2:
        return "", ""
    return lines[0], lines[1]


class SessionExpired(RuntimeError):
    """平台会话失效（收到“重新登录”页）"""


class Crawler:
    """课程平台爬虫核心：登录、课程列表、目录/文件解析、文件下载"""

    def __init__(self, log_fn):
        self.log = log_fn
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA})
        self.login_url = SSO_BASE + "/o/authorize/"
        self._state = ""
        self._client_id = ""
        self._redirect_uri = ""
        self.ajax_sid = ""
        self.csrf = ""
        self.courses = []
        self.username = ""
        self.password = ""

    # ---------------- SSO 登录 ----------------

    @staticmethod
    def _field(page, name):
        """取表单里 name=xxx 的隐藏域 value（可能缺失，返回空串）"""
        m = re.search(r'name="{}"\s+value="([^"]*)"'.format(name), page)
        return html.unescape(m.group(1)) if m else ""

    def _parse_login_form(self, page, url):
        self.login_url = url
        self.csrf = self._field(page, "flowId")
        self._state = self._field(page, "state")
        self._client_id = self._field(page, "client_id")
        self._redirect_uri = self._field(page, "redirect_uri")
        if not (self.csrf and self._redirect_uri):
            raise RuntimeError("登录页解析失败，页面结构可能已变更")

    def fetch_login_page(self):
        """访问 MIS 门户，记录被重定向到的统一身份认证(SSO)授权页参数。
        新版 SSO 是 OAuth 授权页，必须带上 flowId/state/redirect_uri 等隐藏字段，
        否则登录 POST 会被直接拒绝。"""
        r = self.session.get(MIS_HOME, timeout=30)
        self._parse_login_form(r.text, r.url)
        return self.login_url

    @staticmethod
    def _login_error(page):
        """从登录失败页里抠出服务器给的错误提示"""
        m = re.search(r"var\s+error\s*=\s*(\{.*?\});", page, re.S)
        if m:
            msg = re.search(r'"(?:message|msg)"\s*:\s*"([^"]+)"', m.group(1))
            if msg:
                return msg.group(1)
        return "账号或密码错误，或触发了验证码校验"

    def login(self, username, password):
        self.username, self.password = username, password
        data = {
            "username": username,
            "password": password,  # 页面虽启用 SM2 加密，但服务端同时接受明文
            "loginType": "username_password",
            "flowId": self.csrf,
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "scope": "",
            "state": self._state,
            "captcha": "",
            "delegator": "",
            "tokenCode": "",
            "continue": "",
            "asserts": "",
            "pageFrom": "",
            "submit": "登录",
        }
        r = self.session.post(SSO_LOGIN, data=data, timeout=30, allow_redirects=False,
                              headers={"Referer": self.login_url,
                                       "Origin": SSO_BASE})
        location = r.headers.get("Location", "")
        if r.status_code not in (301, 302, 303) or "ncallback" not in location:
            raise RuntimeError("登录被拒绝：{}".format(self._login_error(r.text)))
        # 带着授权码回到 MIS 回调地址，换取门户会话（sessionid）
        self.session.get(location, timeout=30, allow_redirects=True)
        if not self.session.cookies.get("sessionid"):
            raise RuntimeError("门户会话建立失败，请重试")
        self._open_platform()
        self.log("平台会话已建立")

    def _open_platform(self):
        """经 MIS 门户"29号应用"入口跳转到课程平台，抓取平台会话号。
        平台有独立会话：直接访问平台首页会被弹到超时页。"""
        self.session.get(MIS_APP_ENTRY, timeout=30, allow_redirects=True)
        r = self.session.get(PLATFORM + "/ve/back/core/main/index.shtml",
                             params={"method": "index", "type": "qxkt"},
                             timeout=30, allow_redirects=True)
        m = re.search(r"setRequestHeader\(\"sessionId\",\s*'([A-F0-9]+)'", r.text)
        if not m:
            raise RuntimeError("平台会话建立失败（可能未注册课程平台），请重试")
        self.ajax_sid = m.group(1)

    def refresh_session(self):
        """平台会话失效后自动恢复：SSO 会话还在就直接重进平台，
        连 SSO 都过期了就用已保存的账号密码重新登录一次。"""
        r = self.session.get(MIS_HOME, timeout=30)
        if "flowId" in r.text:
            self._parse_login_form(r.text, r.url)
            self.login(self.username, self.password)
            return True
        self._open_platform()
        return True

    # ---------------- 平台 AJAX ----------------

    def _ajax(self, url, params=None):
        headers = {"X-Requested-With": "XMLHttpRequest",
                   "sessionId": self.ajax_sid,
                   "Referer": PLATFORM + "/ve/back/coursePlatform/coursePlatform.shtml"}
        for attempt in (1, 2):
            r = self.session.get(url, params=params, timeout=20, headers=headers)
            if r.status_code == 200 and "重新登录" not in r.text:
                return r
            if attempt == 1 and self.username:
                # 会话被平台判失效（挂机超时、别处登录顶号等）：自动重登再试一次
                self.log("平台会话失效，正在自动重新登录...")
                try:
                    self.refresh_session()
                except Exception as exc:
                    raise SessionExpired("自动重新登录失败：{}".format(exc))
                headers["sessionId"] = self.ajax_sid
                continue
            raise SessionExpired("平台会话已过期，请重新登录")
        raise SessionExpired("平台会话已过期，请重新登录")

    # ---------------- 课程列表 ----------------

    def load_terms(self):
        """返回所有学期列表 [{xqCode, xqName, currentFlag, ...}]
        currentFlag==2 表示当前学期"""
        r = self._ajax(PLATFORM + "/ve/back/rp/common/teachCalendar.shtml",
                       params={"method": "queryCurrentXq"})
        return r.json().get("result", [])

    def load_courses(self, xq_code=None):
        """加载指定学期的课程。
        xq_code 为 None 时自动取 currentFlag==2 的当前学期；
        传入学期代码则拉取该学期课程（支持爬历史学期）。
        """
        if xq_code is None:
            terms = self.load_terms()
            xq_code = next((t["xqCode"] for t in terms
                            if t.get("currentFlag") == 2), None)
            if not xq_code:
                # 兜底：未找到当前学期标记时取列表第一个
                xq_code = terms[0]["xqCode"] if terms else ""
        r = self._ajax(PLATFORM + "/ve/back/coursePlatform/course.shtml",
                       params={"method": "getCourseList", "pagesize": 100,
                               "page": 1, "xqCode": xq_code})
        self.courses = r.json().get("courseList", [])
        return self.courses

    # ---------------- 课件目录与文件 ----------------

    def load_folders(self, course):
        """返回电子课件下的目录树 [{id,name,pId}]"""
        r = self._ajax(PLATFORM + "/ve/back/coursePlatform/courseResource.shtml",
                       params={"method": "stuQueryCourseResourceBag",
                               "courseId": course["course_num"],
                               "cId": course["id"],
                               "xkhId": course["fz_id"],
                               "xqCode": course["xq_code"],
                               "docType": 1})
        return r.json().get("nodes", [])

    def load_files(self, course, folder_id):
        """返回某目录下的文件列表 resList"""
        r = self._ajax(PLATFORM + "/ve/back/coursePlatform/courseResource.shtml",
                       params={"method": "stuQueryUploadResourceForCourseList",
                               "courseId": course["course_num"],
                               "cId": course["id"],
                               "xkhId": course["fz_id"],
                               "xqCode": course["xq_code"],
                               "docType": 1,
                               "up_id": folder_id,
                               "searchName": ""})
        data = r.json()
        return data.get("resList") or []

    # ---------------- 直链获取与下载 ----------------

    def get_play_url(self, f):
        """通过官方接口直接获取文件直链（kk 服务器），
        返回的是 onlinePreview?url=Base64 形式，解码为真实文件路径"""
        r = self._ajax(PLATFORM + "/ve/back/coursePlatform/dataSynAction.shtml",
                       params={"method": "getFilePlayUrl",
                               "id": f.get("resId", ""), "type": 2})
        try:
            url = r.json().get("url")
        except ValueError:
            return None
        if not url:
            return None
        m = re.search(r"onlinePreview\?url=([A-Za-z0-9+/=]+)", url)
        if not m:
            return url
        encoded = m.group(1)
        encoded += "=" * (-len(encoded) % 4)
        return base64.b64decode(encoded).decode("utf-8", "ignore")

    @staticmethod
    def derive_original(kk_url, ext):
        """由转换版 URL + 真实扩展名，推导原件直链
        转换版: /kk/rp/2026/09/11/swf/20260911165934778357.pdf
        原件:  /kk/rp/2026/09/11/20260911165934778357.docx
        """
        m = re.match(r"(.*?/rp/\d{4}/\d{2}/\d{2})/swf/(\d+)\.\w+", kk_url)
        if not m or not ext:
            return None
        return "{}/{}.{}".format(m.group(1), m.group(2), ext.lstrip("."))

    def download(self, url, dest):
        """流式下载到 dest，返回是否成功"""
        r = self.session.get(url, stream=True, timeout=90)
        if r.status_code != 200 or len(r.content) < 200:
            return False
        tmp = dest + ".part"
        with open(tmp, "wb") as fh:
            for chunk in r.iter_content(8192):
                fh.write(chunk)
        os.replace(tmp, dest)
        return True


class App:
    """Tkinter 图形界面"""

    def __init__(self, root):
        self.root = root
        self.crawler = Crawler(self.log)
        self.terms = []          # 学期列表 [{xqCode, xqName, currentFlag, ...}]
        self.current_term = None  # 当前选中的学期对象
        self.course = None
        self.folders = []
        self.files = []
        self.saved_user, self.saved_pwd = load_config()
        self.build_ui()

    # ---------- 界面搭建 ----------

    def build_ui(self):
        self.root.title("北交大课程平台 · 课件爬虫")
        self.root.geometry("780x620")

        top = ttk.LabelFrame(self.root, text="① 登录（账号密码自动读取，一键登录）")
        top.pack(fill="x", padx=10, pady=6)
        row = ttk.Frame(top); row.pack(fill="x", pady=6, padx=8)
        ttk.Label(row, text="账号: {}".format(self.saved_user or "未配置!"),
                  foreground=("#0a7a3d" if self.saved_user else "#c00")).pack(side="left")
        ttk.Button(row, text="登 录", command=self.thread(self.do_login)).pack(side="left", padx=14)
        ttk.Label(row, text="登录后自动加载学期与课程，无需输入验证码",
                  foreground="#666").pack(side="left")

        mid = ttk.LabelFrame(self.root,
                             text="② 选择学期 / 课程 / 目录（切学期自动刷新课程；选课后文件自动加载）")
        mid.pack(fill="x", padx=10, pady=6)
        # 学期行
        row_term = ttk.Frame(mid); row_term.pack(fill="x", pady=(4, 2), padx=8)
        ttk.Label(row_term, text="学期:").pack(side="left")
        self.term_combo = ttk.Combobox(row_term, state="readonly", width=30)
        self.term_combo.pack(side="left", padx=4)
        self.term_combo.bind("<<ComboboxSelected>>",
                             lambda e: self.thread(self.on_term_pick)())
        ttk.Button(row_term, text="切换学期",
                   command=self.thread(self.on_term_pick)).pack(side="left", padx=6)
        # 课程 + 目录行
        row2 = ttk.Frame(mid); row2.pack(fill="x", pady=4, padx=8)
        self.course_combo = ttk.Combobox(row2, state="readonly", width=34)
        self.course_combo.pack(side="left")
        ttk.Button(row2, text="刷新课程", command=self.thread(self.do_refresh_courses)).pack(side="left", padx=6)
        ttk.Label(row2, text=" 目录:").pack(side="left", padx=(8, 0))
        self.folder_combo = ttk.Combobox(row2, state="readonly", width=14)
        self.folder_combo.pack(side="left", padx=3)
        self.folder_combo.bind("<<ComboboxSelected>>",
                               lambda e: self.thread(self.do_load_files)())
        ttk.Button(row2, text="加载文件", command=self.thread(self.do_load_files)).pack(side="left", padx=8)

        bottom = ttk.LabelFrame(self.root, text="③ 勾选文件（点击行选中，再按空格勾选）")
        bottom.pack(fill="both", expand=True, padx=10, pady=6)
        cols = ("name", "ext", "size", "status")
        self.tree = ttk.Treeview(bottom, columns=cols, show="headings",
                                  selectmode="extended")
        self.tree.heading("name", text="文件名")
        self.tree.heading("ext", text="类型")
        self.tree.heading("size", text="大小MB")
        self.tree.heading("status", text="状态")
        self.tree.column("name", width=400)
        self.tree.column("ext", width=52, anchor="center")
        self.tree.column("size", width=70, anchor="e")
        self.tree.column("status", width=120, anchor="center")
        vsb = ttk.Scrollbar(bottom, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.tag_configure("checked", background="#D8EAF6")
        # 按钮栏必须先 pack 到底部整行：若排在会扩展的文件列表之后，会被挤进
        # 列表与滚动条之间的窄缝里，导致下载按钮显示不出来
        bar = ttk.Frame(bottom)
        bar.pack(side="bottom", fill="x", pady=(4, 5), padx=6)
        ttk.Button(bar, text="全选", command=lambda: self.toggle_all(True)).pack(side="left")
        ttk.Button(bar, text="取消全选", command=lambda: self.toggle_all(False)).pack(side="left", padx=6)
        download_btn = ttk.Button(bar, text="开始下载", command=self.thread(self.do_download))
        download_btn.pack(side="left", padx=12)
        self.tree.bind("<space>", self.toggle_selected)
        vsb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True, padx=4, pady=(4, 0))

        self.log_text = tk.Text(self.root, height=7, state="disabled",
                                font=("Consolas", 9))
        self.log_text.pack(fill="x", padx=10, pady=(0, 10))

    # ---------- 工具 ----------

    def log(self, msg):
        self.root.after(0, self._log_impl, msg)

    def _log_impl(self, msg):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", "[{}] {}\n".format(time.strftime("%H:%M:%S"), msg))
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def thread(self, fn):
        def wrapper():
            threading.Thread(target=fn, daemon=True).start()
        return wrapper

    # ---------- 登录 ----------

    def do_login(self):
        if not (self.saved_user and self.saved_pwd):
            messagebox.showerror(
                "缺少账号配置",
                "未读取到账号密码。\n请在文件夹中编辑 账号配置.txt：\n第1行=学号，第2行=密码",
                parent=self.root)
            return
        try:
            self.log("正在登录（账号 {}）...".format(self.saved_user))
            self.crawler.fetch_login_page()
            self.crawler.login(self.saved_user, self.saved_pwd)
            self.log("登录成功，正在获取学期列表...")
            self.terms = self.crawler.load_terms()
            if not self.terms:
                raise RuntimeError("未取到任何学期数据")
            # 默认选中当前学期（currentFlag==2），找不到则取最后一个
            default_idx = next((i for i, t in enumerate(self.terms)
                                 if t.get("currentFlag") == 2),
                                len(self.terms) - 1)
            self.term_combo["values"] = [
                "{}{}".format(t.get("xqName") or t.get("xqCode", ""),
                              "（当前）" if t.get("currentFlag") == 2 else "")
                for t in self.terms]
            self.term_combo.current(default_idx)
            self.current_term = self.terms[default_idx]
            self.log("学期列表已加载（共 {} 个学期），默认为「{}」".format(
                len(self.terms), self.current_term.get("xqName") or self.current_term.get("xqCode")))
            # 触发一次学期切换：拉取当前学期课程
            self.on_term_pick()
            self.course_combo.bind("<<ComboboxSelected>>",
                                   lambda e: self.on_course_pick())
        except Exception as exc:
            messagebox.showerror("登录失败", str(exc), parent=self.root)
            self.log("登录失败: {}".format(exc))

    def on_term_pick(self):
        """切换学期后加载该学期的课程列表"""
        if not self.crawler.ajax_sid:
            messagebox.showinfo("提示", "请先登录", parent=self.root)
            return
        idx = self.term_combo.current()
        if idx < 0 or idx >= len(self.terms):
            return
        self.current_term = self.terms[idx]
        xq_code = self.current_term.get("xqCode", "")
        try:
            self.log("正在加载「{}」的课程列表...".format(
                self.current_term.get("xqName") or xq_code))
            courses = self.crawler.load_courses(xq_code)
            self._populate_courses(courses)
        except Exception as exc:
            messagebox.showerror("错误", str(exc), parent=self.root)
            self.log("学期课程加载失败: {}".format(exc))

    def _populate_courses(self, courses):
        """把课程列表填进下拉框并自动选第一门"""
        names = ["{}（{}）".format(c["name"], c.get("teacher_name", ""))
                 for c in courses]
        self.course_combo["values"] = names
        if courses:
            self.course_combo.current(0)
            self.on_course_pick()
        else:
            # 该学期没有课程时清空目录与文件列表
            self.folder_combo["values"] = ["（无目录）"]
            self.folder_combo.set("（无目录）")
            for item in self.tree.get_children():
                self.tree.delete(item)
        self.log("共 {} 门课程，选择课程和目录后文件自动加载".format(len(courses)))

    def do_refresh_courses(self):
        """重新从平台拉取最新课程列表（按当前选中的学期）"""
        if not self.crawler.ajax_sid:
            messagebox.showinfo("提示", "请先登录", parent=self.root)
            return
        if not self.current_term:
            messagebox.showinfo("提示", "请先选择学期", parent=self.root)
            return
        try:
            self.log("正在重新拉取「{}」的课程列表...".format(
                self.current_term.get("xqName") or self.current_term.get("xqCode")))
            courses = self.crawler.load_courses(self.current_term.get("xqCode", ""))
            self._populate_courses(courses)
        except Exception as exc:
            messagebox.showerror("错误", str(exc), parent=self.root)
            self.log("刷新失败: {}".format(exc))

    def on_course_pick(self):
        """选课后立即拉取该课的课件目录，并自动加载第一个目录的文件"""
        idx = self.course_combo.current()
        if idx < 0 or not self.crawler.courses:
            return
        self.course = self.crawler.courses[idx]
        try:
            self.folders = self.crawler.load_folders(self.course)
            names = [f["name"] for f in self.folders if str(f.get("pId")) == "0"]
            if not names:
                names = [f["name"] for f in self.folders]
            self.folder_combo["values"] = names or ["（无目录）"]
            if names:
                self.folder_combo.current(0)
                self.thread(self.do_load_files)()
            self.log("《{}》共 {} 个课件目录".format(self.course["name"], len(names)))
        except Exception as exc:
            self.log("目录加载失败: {}".format(exc))

    # ---------- 文件列表 ----------

    def do_load_files(self):
        if not self.course:
            messagebox.showinfo("提示", "请先登录并选择课程", parent=self.root)
            return
        fname = self.folder_combo.get()
        folder = next((f for f in self.folders if f["name"] == fname), None)
        if folder is None:
            messagebox.showinfo("提示", "请选择目录", parent=self.root)
            return
        try:
            self.log("正在加载「{}」的文件...".format(fname))
            self.files = self.crawler.load_files(self.course, folder["id"])
            for item in self.tree.get_children():
                self.tree.delete(item)
            for i, f in enumerate(self.files):
                self.tree.insert("", "end", iid=str(i),
                                 values=(f.get("rpName", ""),
                                         f.get("extName", ""),
                                         f.get("rpSize", ""),
                                         "待下载"))
            self.log("解析到 {} 个文件".format(len(self.files)) if self.files
                     else "该目录暂无文件")
        except Exception as exc:
            messagebox.showerror("错误", str(exc), parent=self.root)
            self.log("加载失败: {}".format(exc))

    # ---------- 勾选 ----------

    def toggle_selected(self, _event=None):
        for item in self.tree.selection():
            tags = self.tree.item(item, "tags")
            self.tree.item(item, tags=() if "checked" in (tags or ()) else ("checked",))
        return "break"

    def toggle_all(self, check):
        for item in self.tree.get_children():
            self.tree.item(item, tags=("checked",) if check else ())

    # ---------- 下载 ----------

    def do_download(self):
        if not self.files:
            messagebox.showinfo("提示", "请先加载文件列表", parent=self.root)
            return
        checked = [i for i in self.tree.get_children()
                   if "checked" in (self.tree.item(i, "tags") or ())]
        if not checked:
            messagebox.showinfo("提示", "请先点击行选中文件，再按空格勾选", parent=self.root)
            return
        base_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "downloads", self.course["name"],
                                self.folder_combo.get().replace(os.sep, "_"))
        os.makedirs(base_dir, exist_ok=True)
        self.log("开始下载 {} 个文件 -> {}".format(len(checked), base_dir))
        for item in checked:
            self._download_one(self.files[int(item)], base_dir, item)
        self.log("全部任务完成")

    def _download_one(self, f, base_dir, tree_item):
        name = re.sub(r'[\\/:*?"<>|]', "_", f.get("rpName", "unnamed"))
        ext = f.get("extName", "")
        self.tree.set(tree_item, "status", "解析直链...")
        try:
            kk_url = self.crawler.get_play_url(f)
            if not kk_url:
                raise RuntimeError("未取到预览直链")
            original = self.crawler.derive_original(kk_url, ext)
            # 优先原件，失败则下载转换版 PDF
            for kind, url, real_ext in (("原件", original, ext or ""),
                                        ("预览版", kk_url, ".pdf")):
                if not url:
                    continue
                dest = os.path.join(base_dir, name + real_ext
                                    if real_ext.startswith(".")
                                    else (name + "." + real_ext if real_ext else name))
                if not os.path.splitext(dest)[1]:
                    dest += ".pdf"
                if os.path.exists(dest) and os.path.getsize(dest) > 200:
                    self.tree.set(tree_item, "status", "已存在")
                    self.log("跳过（已存在）: {}".format(name))
                    return
                if self.crawler.download(url, dest):
                    self.tree.set(tree_item, "status", "OK({})".format(kind))
                    self.log("下载成功[{}]: {}{}".format(kind, name, real_ext))
                    return
            self.tree.set(tree_item, "status", "失败")
            self.log("下载失败: {}".format(name))
        except Exception as exc:
            self.tree.set(tree_item, "status", "失败")
            self.log("下载出错 {}: {}".format(name, exc))


def main():
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
