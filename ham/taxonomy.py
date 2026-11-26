from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class NoiseCategory:
    code: str
    group: str
    group_label: str
    label: str
    hint: str


@dataclass(frozen=True)
class SourceDirection:
    code: str
    label: str
    short: str


# 5 大类 × 19 项精细化室内外噪声行为分类（覆盖结构撞击、人声冲突、影音外放、居家施工与环境）
NOISE_CATEGORIES: tuple[NoiseCategory, ...] = (
    # 1. 结构撞击与移位
    NoiseCategory("jumping_stomping", "impact", "结构撞击与移位", "蹦跳 / 重踩楼板", "明显天花板闷响、剧烈跳跃或重脚跟踩踏"),
    NoiseCategory("running_pacing", "impact", "结构撞击与移位", "室内跑步 / 急促走动", "连续节奏性脚步声、来回跑动"),
    NoiseCategory("furniture_drag", "impact", "结构撞击与移位", "桌椅家具拖拽移位", "拉椅子、挪床、推桌子摩擦楼板震动"),
    NoiseCategory("moving_heavy_objects", "impact", "结构撞击与移位", "搬运重物 / 坠落砸地", "重物落地闷响、持续搬东西挪动"),
    NoiseCategory("door_slamming", "impact", "结构撞击与移位", "重摔门 / 甩柜门", "入户门、房门或衣柜门剧烈撞击声"),
    # 2. 人声喧哗与情绪冲突
    NoiseCategory("shouting_screaming", "vocal", "人声喧哗与冲突", "突然尖叫 / 直播鬼叫", "深夜亢奋喊叫、游戏/直播突发尖叫"),
    NoiseCategory("loud_singing", "vocal", "人声喧哗与冲突", "高歌 / K歌喊麦", "大声唱歌、麦霸练歌或连麦喊麦"),
    NoiseCategory("loud_talking", "vocal", "人声喧哗与冲突", "大声讲话 / 聚会喧哗", "深夜多人聊天大笑、高声打电话"),
    NoiseCategory("quarreling_arguing", "vocal", "人声喧哗与冲突", "激烈吵架 / 争执冲突", "争吵咆哮、拍桌摔物伴随争执"),
    NoiseCategory("child_crying", "vocal", "人声喧哗与冲突", "小孩哭闹 / 持续啼哭", "婴幼儿或儿童深夜大声哭泣喊叫"),
    # 3. 影音与乐器外放
    NoiseCategory("loud_audio_speaker", "media", "影音与乐器外放", "音响 / 电视高音量外放", "音箱、电视、投影或手机高音量播放"),
    NoiseCategory("bass_subwoofer", "media", "影音与乐器外放", "低音炮 / 节奏鼓点共振", "穿透力极强的低频音乐节奏与墙体共振"),
    NoiseCategory("musical_instrument", "media", "影音与乐器外放", "乐器弹奏 / 练习", "深夜钢琴、吉他、架子鼓等乐器声"),
    # 4. 居家高频操作与施工
    NoiseCategory("kitchen_chopping", "domestic", "居家操作与施工", "切菜 / 剁骨 / 厨具敲击", "厨房案板高频剁切、锅碗瓢盆剧烈碰撞"),
    NoiseCategory("drilling_renovation", "domestic", "居家操作与施工", "打孔电钻 / 敲墙施工", "电钻冲击钻、电锤砸墙或违规时段装修"),
    NoiseCategory("appliance_vibration", "domestic", "居家操作与施工", "家电 / 设备周期共振", "空调外机、洗衣机脱水、水泵或电梯低频嗡鸣"),
    # 5. 环境与其他
    NoiseCategory("pet_noise", "ambient", "环境与其他", "宠物吠叫 / 挠门跑酷", "猫狗深夜持续吠叫、抓门或追逐跑跳"),
    NoiseCategory("street_traffic", "ambient", "环境与其他", "窗外车流 / 室外环境", "马路飙车、鸣笛、垃圾清运或室外广场声"),
    NoiseCategory("other_unknown", "ambient", "环境与其他", "其他 / 暂未归类", "无法归入上述类型的异常声响"),
)

# 8 类声源方位归属（与噪声类型解耦，如实记录“确定”与“待定”，守住证据客观性）
SOURCE_DIRECTIONS: tuple[SourceDirection, ...] = (
    SourceDirection("upstairs", "正楼上（明确）", "正楼上"),
    SourceDirection("diagonal_upstairs", "斜楼上（疑似）", "斜楼上"),
    SourceDirection("downstairs", "楼下", "楼下"),
    SourceDirection("adjacent", "同层隔壁", "同层隔壁"),
    SourceDirection("public_facility", "楼道 / 电梯 / 公共设施", "公共设施"),
    SourceDirection("outdoor", "窗外 / 室外道路", "室外"),
    SourceDirection("self_household", "本户误触（已排除）", "本户排除"),
    SourceDirection("uncertain", "方位待定（暂无法确认）", "方位待定"),
)

CATEGORY_MAP: Dict[str, NoiseCategory] = {c.code: c for c in NOISE_CATEGORIES}
DIRECTION_MAP: Dict[str, SourceDirection] = {d.code: d for d in SOURCE_DIRECTIONS}

# 向后兼容 Phase 1 粗粒度旧标签的展示名映射
LEGACY_LABEL_ALIASES: Dict[str, str] = {
    "upstairs_impact": "蹦跳 / 重踩楼板（旧标签）",
    "upstairs_vocal": "突然尖叫 / 直播鬼叫（旧标签）",
    "upstairs_mixed": "撞击 + 人声混合（旧标签）",
    "self_noise": "本户误触排除（旧标签）",
    "street_noise": "窗外车流 / 室外环境（旧标签）",
    "appliance_hum": "家电 / 设备周期共振（旧标签）",
}


def get_category_label(code: Optional[str]) -> str:
    if not code:
        return "未分类"
    if code in CATEGORY_MAP:
        return CATEGORY_MAP[code].label
    if code in LEGACY_LABEL_ALIASES:
        return LEGACY_LABEL_ALIASES[code]
    return code


def get_category_group(code: Optional[str]) -> tuple[str, str]:
    if not code:
        return ("unclassified", "未分类")
    if code in CATEGORY_MAP:
        c = CATEGORY_MAP[code]
        return (c.group, c.group_label)
    if code in ("upstairs_impact",):
        return ("impact", "结构撞击与移位")
    if code in ("upstairs_vocal", "upstairs_mixed"):
        return ("vocal", "人声喧哗与冲突")
    if code in ("appliance_hum",):
        return ("domestic", "居家操作与施工")
    return ("ambient", "环境与其他")


def get_direction_label(code: Optional[str], short: bool = False) -> str:
    if not code:
        return "方位待定"
    if code in DIRECTION_MAP:
        return DIRECTION_MAP[code].short if short else DIRECTION_MAP[code].label
    return code


def taxonomy_payload() -> dict:
    """Serialize grouped categories and directions for API / CLI display."""
    groups_order: List[str] = []
    grouped: Dict[str, dict] = {}
    for c in NOISE_CATEGORIES:
        if c.group not in grouped:
            groups_order.append(c.group)
            grouped[c.group] = {
                "group": c.group,
                "group_label": c.group_label,
                "items": [],
            }
        grouped[c.group]["items"].append(
            {
                "code": c.code,
                "label": c.label,
                "hint": c.hint,
            }
        )
    return {
        "category_groups": [grouped[g] for g in groups_order],
        "directions": [
            {"code": d.code, "label": d.label, "short": d.short}
            for d in SOURCE_DIRECTIONS
        ],
    }
