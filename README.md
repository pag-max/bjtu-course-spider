# 北交大课程平台课件爬虫 (bjtu-course-spider)

自动登录北京交通大学课程平台，把某门课的全部课件按目录结构批量下载到本地。
带 Tkinter 图形界面，不写命令行也能用。

![python](https://img.shields.io/badge/Python-3.8%2B-3776AB) ![license](https://img.shields.io/badge/license-MIT-blue)

## 功能

- SSO 登录（学号 + 密码），登录态用 Cookie 维持
- 列出全部课程，勾选需要的课
- 递归遍历课程章节树，按 `课程名/目录名/文件名` 保存
- 已下载的自动跳过，中断可续传
- 下载进度、日志实时显示在界面上

## 环境

只需要 `requests`：

```bash
pip install -r requirements.txt
```

## 使用

1. 复制 `账号配置.example.txt` 为 `账号配置.txt`，第一行学号、第二行密码
2. 双击 `启动爬虫.bat`，或 `python 课件爬虫.py`
3. 选课 → 选保存目录 → 开始

`启动爬虫.bat` 会依次尝试 `py` / `python` / `pythonw`，找不到才回落到固定路径，不依赖某一台机器的安装位置。

## 目录

```
课件爬虫.py       主程序（Tkinter GUI + 爬取逻辑）
run_spider.py     启动辅助（供 bat 调用）
启动爬虫.bat       一键启动
账号配置.example.txt  账号模板
使用说明.txt       详细图文说明
requirements.txt  依赖
```

## 注意

- 请遵守学校平台的使用规定，控制并发，不要给服务器造成压力
- 本项目仅供个人备份自己有权访问的课件
