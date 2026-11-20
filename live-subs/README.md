# Live Subs：俄语实时字幕 + 翻译

把电脑里播放的俄语（Twitch 主播、Dota 2 语音、YouTube）实时转成俄语字幕，并翻译成中文。字幕显示在一个置顶的小窗口里。

- 只录电脑**播放**出来的声音，**不会**录你的麦克风。
- 语音识别（Whisper）在你自己的显卡上运行。翻译用的是 Google 翻译，需要联网。
- 每一句都会保存到 `transcripts/日期.txt`，之后可以复制到 Russian Lane 里学习。

## 第一次安装（大约 10–20 分钟，大部分时间在下载）

1. 安装 **Python 3.12**：https://www.python.org/downloads/
   安装时一定要勾选 **"Add python.exe to PATH"**。
2. 把这个 `live-subs` 文件夹下载到电脑上，比如 `D:\live-subs`。
3. 双击 **`install.bat`**，等它跑完。它会下载 1–3 GB 的显卡相关文件。

## 每次使用

双击 **`run.bat`**。第一次运行还会下载语音模型（约 1.6 GB），以后就不用了。

- 用鼠标拖动窗口可以移动它，拖右下角 ◢ 可以改大小。
- 按钮：`abc` 显示/隐藏拉丁字母读音，`A− A+` 调字号，`⏸` 暂停，`✕` 关闭。也可以按 Esc 关闭。

想用英文翻译，或者调其他设置，就在这个文件夹里打开命令行运行：

```
run.bat --target en        英文翻译
run.bat --target none      只显示俄语，不翻译（最适合练听力）
run.bat --lang uk          识别乌克兰语（比如 SadSvit 的歌）
run.bat --lines 5          显示 5 行
run.bat --list-devices     列出音频设备
run.bat --help             所有设置
```

## 翻译：免费 Google 还是 Claude

- **默认：免费 Google 翻译。**请求太多时，Google 会暂时拒绝。程序会把几句合并成一次请求；被拒绝后，先改用 MyMemory（另一个免费翻译）顶 2 分钟，再试 Google。缺点是不懂游戏黑话和脏话。
- **更好：Claude 翻译。**它懂俚语、骂人话和 Dota 术语，能结合前几句理解上下文，也会自动纠正一部分识别错误。需要付费的 API key，按用量计费。
  1. 在 https://platform.claude.com 注册，充值，然后创建一个 API key。
  2. 在 cmd 里运行下面这行（把 `你的key` 换成你自己的 key），然后**关掉 cmd 重新打开**：
     ```
     setx ANTHROPIC_API_KEY "你的key"
     ```
  3. 再运行 `run.bat`，状态栏会显示 `Claude`。
  - 想更便宜：`run.bat --claude-model claude-haiku-5-5`。大约便宜 40 倍，翻译质量会差一些。
  - 想临时改回 Google：`run.bat --translator google`
  - **API key 不要发给任何人，也不要贴到聊天里。**

## Dota 2 里要注意

- 字幕窗口要显示在游戏上面，Dota 必须用 **无边框窗口（Borderless Window）** 模式。设置位置：设置 → 视频 → 显示模式。如果用独占全屏，窗口会被游戏盖住。
- 别人的游戏语音能识别；文字聊天识别不了，因为那不是声音。

## 有问题？

- **没有字幕**：先确认声音是从默认播放设备（耳机/扬声器）出来的。运行 `run.bat --list-devices`，然后用 `run.bat --device-index 数字` 指定设备。
- **很慢 / 状态栏显示 CPU**：说明显卡没用上。把黑色窗口里的错误信息复制给 Claude。
- **识别错得多**：主播语速快、背景音乐大、几个人同时说话的时候，识别本来就会出错。这是正常的。
