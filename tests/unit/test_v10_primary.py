# -*- coding: utf-8 -*-
"""v10 M2 主靶 no_capture 第三轮。"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "src" / "backend"
LABEL = Path(__file__).resolve().parents[2] / "label_project"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(LABEL))

from qwen_ollama import (  # noqa: E402
    apply_classification_post_rules,
    explain_positive_capture_block,
    load_l2_whitelist,
)


def _cap(text: str, model_l1: str, diag_ok: tuple = ("eligible:v10_no_capture",)) -> None:
    diag = explain_positive_capture_block(text)
    assert diag in diag_ok, diag
    l2_map = load_l2_whitelist()
    l1, l2, flags = apply_classification_post_rules(text, model_l1, "", l2_map)
    assert l1 == "非问题" and l2 == "" and flags.get("pos_captured")


def _not_cap(text: str, model_l1: str) -> None:
    l2_map = load_l2_whitelist()
    l1, _, flags = apply_classification_post_rules(text, model_l1, "", l2_map)
    assert l1 == model_l1 and not flags.get("pos_captured")


def test_remote_tailgate():
    _cap(
        "重庆- KELIN-Eletre 18983088858 LJUBMSA16PK004311 人在外地，要求远程为其关下后尾门",
        "服务类",
    )


def test_accident_coord():
    _cap(
        "杭州-王德安先生-Eletre，18916638752，车辆事故，无人员受伤，已经报了保险和交警，需要对接售后协助您车辆定损和维修",
        "服务类",
    )


def test_soft_it_feedback():
    _cap(
        "车主换手机机型后其他操空人都要跟着换，这是啥操作啊。请你向你们信息技术部或者领导反馈一下。[2026-05-23 20:45:58]",
        "产品质量类",
    )


def test_lifestyle_positive_share():
    _cap(
        "夜间去专属充电桩充个电，速度飞起 第二天直接冲向三洲田，雨后的三洲田格外美丽，雾气朦胧，驾驶这辆千匹野兽在盘山公路间穿梭，乐趣无穷??",
        "产品质量类",
    )


def test_free_service_ask():
    _cap(
        "孔先生 15057739776  来源：专属服务群 用户反馈：售后这边可以免费除玻璃上的油膜吗？前挡玻璃有点模糊，晚上路灯感觉有光晕射出来",
        "服务类",
    )


def test_valet_move():
    _cap(
        "李欣宇；17770040640；用户反馈：想将车辆从一个地方代驾到另一个地方，车辆无售后无质量问题[2026-05-24 17:59:09]",
        "服务类",
    )


def test_missing_parts():
    _cap(
        "林晓晨，18868803165，单号：OR2605218603gfZn，客户反馈接缝处少了两个配件[2026-05-24 13:35:47]",
        "产品质量类",
    )


def test_tow_flat():
    _cap(
        "阮婷婷  15157695500  LJUBMSA12RK007550  用户轮胎扎钉漏气，需要拖车[2026-05-13 09:01:38]",
        "服务类",
    )


def test_mall_strap_relay():
    _cap(
        "李女士 13911236487  商城购买的商品缺少背带，致电告知用户可以先退，再重新拍，用户未接通[2026-05-18 15:43:37]",
        "服务类",
    )


def test_resolved_unlock():
    _cap(
        "13161633003  用户表示车辆解锁不了，ELETRE车辆，使用的三角钥匙，随后表示已成功解锁车辆[2026-05-18 07:21:41]",
        "产品质量类",
    )


def test_seat_colloquial_not_captured():
    _not_cap("座椅嘎嘣硬，疼的我嗷嗷叫", "体验需求类")


def test_insurance_premium_not_captured():
    _not_cap(
        "没有按照我想要的时间正确启动和停止，所以我最终要支付保费！",
        "服务类",
    )


def test_return_not_captured():
    _not_cap(
        "无锡-瞿子伟先生-Emeya反馈：遮阳棚质感太一般，想要退货",
        "产品质量类",
    )


def test_frustrated_recall_not_captured():
    _not_cap(
        "我写信表达我对我的新Emira持续存在的问题感到沮丧，我只拥有它七个月，但它已经在店里召回了一个多月。",
        "服务类",
    )


def test_deposit_not_captured():
    _not_cap(
        "13915511675，范小驰女士，交付回访，客户不评价，表示定金两万还没有退，以及两个车模也没有给自己[2026-05-08 19:13:08]",
        "服务类",
    )


def test_order_account_coord():
    _cap(
        "客户信息:汤望,汤望,号码13927398877,爱人 号码13433599231,身份证号码4325011981111031013"
        "反馈:用我自己的手机号码注册了,然后下了小定,然后又用我老婆手机号码注册了一个账号,下了大定,但是小定大定的名字和身份证号码"
        "都是填的我的。我明天准备提车了!但是小定和大定没有联动起来,明天都要提车了。门店说已经给总部发了2次邮件,但10天过去了,都没"
        "有结果[2026-04-2822:43:32]",
        "服务类",
    )


def test_english_tow_relay():
    _cap(
        "Vehicle towed to Bradfordlast Thursday CST has not been able to reach BradfordMay also need "
        "CC if repair is delayed. - MF75KNU (private plate C1KSN also on record w/ VIN9",
        "服务类",
    )


def test_dead_battery_relay():
    _cap(
        "何浩焜,17701731009,ljulmsa35tk003358,车型f***me,客户反馈车辆目前没有电了,打不开车门[2026-04-28 20:20:49]",
        "产品质量类",
    )


def test_poetic_drive_share():
    text = (
        "路特斯第二天,真的太对味了\n"
        "她就像一个英国小女孩\n"
        "市区溜达、接娃、放空,她就是那种你能开一整天都不不累的车\n"
        "那种瞬间推背感,会让你突然笑起来"
    )
    _cap(text, "产品质量类")


def test_server_text_variants():
    """服务器 CSV 原文常见 OCR/录入变体。"""
    _cap(
        "客户信息:汤望,号码13927398877 反馈:下了小定,又注册了一个账号,下了大定,但是小定和大定没有联动动起来,"
        "明天都要提车了。门店说已经给总部发了2次邮件,但10天过去了,都没有结果",
        "服务类",
    )
    _cap(
        "Vehicle towed to Bradford lastThursday, CST has not been able to reach Bradford. "
        "May alsneed CC if repair is delayed.",
        "服务类",
    )
    _cap(
        "何浩焜,17701731009,客户反馈车辆目前没有电了了,打不开车门",
        "产品质量类",
    )


def test_long_poetic_share_462_chars():
    base = (
        "路特斯第二天，真的太对味了。她就像一个英国小女孩——结果一深踩电门，"
        "悬挂软得像在云上飘，推背感会让你突然笑起来，补能焦虑四个字可以扔了，"
        "这钱花得值。#LotusForMe #电动车真香 "
    )
    text = base + "越开越上瘾。" * 30
    _cap(text, "产品质量类")


def test_minor_collision_coord():
    _cap(
        "杨家瑜，电话：15050300180，反馈车辆发生了摩擦，前保险杠受损，已报交警定责，"
        "需要进店维修，车辆能正常行驶[2026-04-28 13:23:44]",
        "服务类",
    )


def test_soft_sales_system_feedback():
    _cap(
        "产品非常棒，但购车体验有待加强。最难的产品都做好了，好做的销售运营却没做好。"
        "不是针对我的销售，而是说整个莲花的销售体系，购车体验依然是套路满满，"
        "莲花这么好的品牌和产品力，不应该输在这种地方。",
        "服务类",
    )


def test_repair_plan_inquiry():
    _cap(
        "LJUBMSA15RK007445 上面的车有维修计划吗？ 马克·赫斯特 服务经理",
        "服务类",
    )


def test_repair_plan_full_email():
    text = (
        "注意：此邮件来自组织外部，请谨慎打开链接或附件。\n"
        "LJUBMSA15RK007445\n"
        "上面的车有维修计划吗？\n"
        "马克·赫斯特\n服务经理\n"
        "卡芬斯沃克斯豪尔阿什福德，纪念碑路，轨道公园，阿什福德，肯特郡，TN24 0HB\n"
        "T： 01233 504604|W：caffyns.co.uk"
    )
    _cap(text, "服务类")


def test_missing_tool_relay():
    _cap(
        "用户反馈车辆因轮胎漏气需要拆卸轮胎时发现，车辆防盗螺栓的拆卸工具不在车上，"
        "门店告知轮胎螺栓上确实没有拆卸痕迹，自己车上没有相关工具，需要补发",
        "服务类",
    )


def test_brand_design_opinion():
    _cap(
        "客户反馈：在此就游心P1车型高度抄袭EMEYA外观设计一事，我怀揣对路特斯百年超跑品牌的信仰，"
        "花费五十余万购入EMEYA，看中的是其原创的高端轿跑设计、专属的品牌格调与独一无二的产品价值。"
        "全方位照搬EMEYA原创设计，赤裸裸将百万级超跑设计廉价化，套壳化。",
        "服务类",
    )


def test_external_spam_email():
    text = (
        "注意：此邮件来自组织外部，请谨慎打开链接或附件。\n"
        "嗨，我正在检查您的网站，并注意到您可以提高Google可见性和网站流量的一些领域。\n"
        "SEO优化\n谷歌排名改进\n网站速度和技术修复\n当地SEO\n"
        "如果你感兴趣，我可以分享一个简短的网站审计和一些建议。你要我送过去吗？"
    )
    _cap(text, "体验需求类")


def test_tire_flat_overnight():
    _cap(
        "徐先生 用户表示左前轮一晚上下来完全没气了，车停好了。客户需要联系来电号码：13806660595",
        "产品质量类",
    )


def test_mall_return_not_captured():
    _not_cap(
        "15350677878，用户反馈购买的手机支架买重了，还没有送到，想要退货。",
        "服务类",
    )


def test_repair_eta_relay():
    _cap("检查维修，更新CC团队\n维修ETA5月15日-18日", "服务类")


def test_phone_change_assist():
    _cap("严先生 致电协助用户操作修改手机号，接通后无声音", "服务类", ("eligible:v98_no_capture", "eligible:v10_no_capture"))


def test_urgent_contact_relay():
    _cap(
        "用户反馈：关于车辆轮胎问题 怎么还没有售后人员主动联系，自己都快到店了，需要售后人员加急联系",
        "服务类",
    )


def test_mall_launch_ask():
    _cap("forme 后备箱垫不是回复的上、中旬上架吗，怎么还没上？", "服务类")


def test_cat_undercarriage_assist():
    _cap(
        "车底钻进去一只猫，加急到上海售后门店拆底盘检查，大概十五分钟到店，需要售后主动联系",
        "服务类",
    )


def test_staff_callback_relay():
    _cap(
        "致电用户告知锁灯车灯不灭的情况,技术部门答复建议进店处理，用户知悉无异议",
        "体验需求类",
    )


def test_post_accident_tow():
    _cap("出了事故，事故昨天已处理完毕，想要拖车进店", "服务类")


def test_lockout_assist():
    _cap("没带家钥匙,没带手机,家门被锁上了，没有车控功能，需要售后处理联系", "服务类")


def test_nail_tow_relay():
    _cap("右后轮扎钉子了。电话中断，回拨两次客户都拒接了", "服务类")


def test_product_option_feedback():
    _cap("LOTUS马年限定拉花没有FORME的选项", "服务类")


def test_ownership_ota_share():
    _cap(
        "从15岁拿驾照到现在也是老司机了。我选择了刚上市的For Me，重新唤醒了内心驾驶的渴望。"
        "如果后期底盘能通过OTA在舒适模式下更软一点，那就更适配国内路况的多样性了。",
        "体验需求类",
    )
