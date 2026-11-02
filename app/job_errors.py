"""Explain failures without discarding the original diagnostic evidence."""
def explain_error(error):
    raw = str(error)
    text = raw.lower()
    cases = [
        (('素材不可访问', 'no such file', 'not found', '找不到'), '找不到所需文件或共享盘暂时断开', '检查原路径能否打开；共享盘先连接，再用“重新定位”关联同一素材。'),
        (('permission', 'access is denied', '拒绝访问', 'winerror 5'), '没有读取素材或写入项目的权限', '把项目放到可写目录，检查共享盘权限以及文件是否被其他程序占用。'),
        (('no space', 'disk full', '空间不足', 'winerror 112'), '项目所在磁盘空间不足', '释放项目磁盘空间后重试，已经完成的分段会保留。'),
        (('out of memory', 'bad allocation', '内存不足'), '运行内存或显存不足', '关闭占用内存的软件后重试；显卡识别失败时程序会尝试CPU。'),
        (('cuda', 'cublas', 'cudnn', 'nvidia'), '显卡加速组件或驱动运行失败', '检查显卡驱动和GPU组件是否完整；自动回退CPU也失败时，请复制详细错误用于排查。'),
        (('指纹变化', '指纹不同'), '素材与已导入的版本不同', '重新定位原来同一份素材；如果剪辑内容变了，请新建项目版本，避免错用审核进度。'),
        (('invalid data', 'invalid argument', '音视频处理失败', 'wave.error', 'fmt chunk', '无音轨'), '音视频无法正常解码或没有可用音轨', '先用播放器检查该集是否正常且有声音；可重新导出音视频，再作为新版本导入。'),
        (('model', '模型', 'huggingface', 'connection', 'timed out'), '模型文件、网络或设备准备未完成', '完整解压便携包，确认模型文件没有被安全软件隔离；包内模型无需联网下载。'),
    ]
    for needles, title, action in cases:
        if any(needle in text for needle in needles):
            return {'title': title, 'action': action, 'detail': raw}
    return {'title': '本集初查没有完成，错误原因需要进一步排查',
            'action': '可复制详细错误交给维护者；也可跳过本集初查继续文字审查，或将本集从本轮移出。其他集可以继续。', 'detail': raw}
