#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
====================================================================
新能源车企舆情分类系统 V2.4 前端专项优化模块
====================================================================
优化方向：
1. 批量操作交互优化（多选、快捷键）
2. 关键词联想功能
3. 移动端适配优化
4. 视觉美化样式
5. 置信度展示
6. 可视化图表优化
7. 角色适配功能

约束：
- 仅优化前端界面，不修改任何后端分类逻辑
- 保持原有准确率90.64%不变
- 适配PC端为主，移动端兼容
====================================================================
"""

import streamlit as st
import json
import pandas as pd
from datetime import datetime

# ===================== 1. 前端样式配置 =====================
def get_frontend_styles():
    """
    获取前端样式配置
    包含：按钮样式、留白规范、动效配置
    """
    return """
    <style>
    /* ==================== 基础配置 ==================== */
    :root {
        /* 车企简约配色 */
        --primary-color: #1E293B;
        --primary-hover: #121A29;
        --secondary-color: #3B82F6;
        --success-color: #10B981;
        --warning-color: #F59E0B;
        --danger-color: #EF4444;
        
        /* 留白规范 */
        --spacing-xs: 8px;
        --spacing-sm: 12px;
        --spacing-md: 16px;
        --spacing-lg: 20px;
        --spacing-xl: 24px;
        
        /* 列表行高 */
        --row-height-pc: 48px;
        --row-height-mobile: 44px;
        
        /* 圆角 */
        --border-radius: 8px;
    }
    
    /* ==================== 按钮样式优化 ==================== */
    .stButton > button {
        /* PC端 */
        height: 40px;
        font-size: 14px;
        border-radius: var(--border-radius);
        background-color: var(--primary-color);
        color: white;
        border: none;
        transition: all 0.2s ease;
    }
    
    .stButton > button:hover {
        background-color: var(--primary-hover);
        transform: translateY(-1px);
    }
    
    .stButton > button:active {
        transform: translateY(1px);
    }
    
    /* 批量操作按钮 - 滑入动画 */
    .batch-action-btn {
        animation: slideIn 0.3s ease-out;
    }
    
    @keyframes slideIn {
        from {
            opacity: 0;
            transform: translateX(20px);
        }
        to {
            opacity: 1;
            transform: translateX(0);
        }
    }
    
    /* ==================== 卡片样式 ==================== */
    .metric-card {
        background: white;
        border-radius: var(--border-radius);
        padding: var(--spacing-lg);
        box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        transition: all 0.2s ease;
    }
    
    .metric-card:hover {
        box-shadow: 0 4px 12px rgba(0,0,0,0.12);
    }
    
    /* ==================== 置信度样式 ==================== */
    .confidence-badge {
        display: inline-flex;
        align-items: center;
        padding: 4px 8px;
        border-radius: 4px;
        font-weight: bold;
        font-size: 12px;
    }
    
    .confidence-high {
        background-color: rgba(16, 185, 129, 0.1);
        color: #10B981;
    }
    
    .confidence-medium {
        background-color: rgba(245, 158, 11, 0.1);
        color: #F59E0B;
    }
    
    .confidence-low {
        background-color: rgba(239, 68, 68, 0.1);
        color: #EF4444;
    }
    
    /* ==================== 复选框动画 ==================== */
    .stCheckbox > label > div:first-child {
        transition: all 0.2s ease;
    }
    
    .stCheckbox > label > div:first-child:checked {
        animation: checkFadeIn 0.2s ease;
    }
    
    @keyframes checkFadeIn {
        from { opacity: 0.5; }
        to { opacity: 1; }
    }
    
    /* ==================== 移动端适配 ==================== */
    @media (max-width: 768px) {
        .stButton > button {
            height: 36px;
            font-size: 12px;
        }
        
        .metric-card {
            padding: var(--spacing-md);
        }
        
        /* 移动端底部浮层 */
        .mobile-bottom-sheet {
            position: fixed;
            bottom: 0;
            left: 0;
            right: 0;
            background: white;
            border-radius: 16px 16px 0 0;
            padding: var(--spacing-lg);
            box-shadow: 0 -4px 20px rgba(0,0,0,0.15);
            z-index: 1000;
            animation: slideUp 0.3s ease;
        }
        
        @keyframes slideUp {
            from { transform: translateY(100%); }
            to { transform: translateY(0); }
        }
    }
    
    /* ==================== 图表样式 ==================== */
    .chart-container {
        background: white;
        border-radius: var(--border-radius);
        padding: var(--spacing-lg);
        box-shadow: 0 2px 8px rgba(0,0,0,0.08);
    }
    
    /* 异常点标注 */
    .anomaly-point {
        animation: pulse 1s infinite;
    }
    
    @keyframes pulse {
        0%, 100% { opacity: 1; }
        50% { opacity: 0.5; }
    }
    
    /* ==================== 列表样式 ==================== */
    .feedback-list-item {
        display: flex;
        align-items: center;
        padding: var(--spacing-sm) var(--spacing-md);
        border-bottom: 1px solid #E5E7EB;
        transition: background-color 0.2s ease;
    }
    
    .feedback-list-item:hover {
        background-color: #F9FAFB;
    }
    
    .feedback-list-item.selected {
        background-color: #EFF6FF;
    }
    
    /* ==================== 关键词联想下拉 ==================== */
    .keyword-suggestion {
        position: absolute;
        background: white;
        border: 1px solid #E5E7EB;
        border-radius: var(--border-radius);
        max-height: 200px;
        overflow-y: auto;
        z-index: 100;
        box-shadow: 0 4px 12px rgba(0,0,0,0.1);
    }
    
    .keyword-suggestion-item {
        padding: 8px 12px;
        cursor: pointer;
        transition: background-color 0.15s ease;
    }
    
    .keyword-suggestion-item:hover {
        background-color: #F3F4F6;
    }
    
    /* ==================== 角色标签 ==================== */
    .role-badge {
        display: inline-flex;
        align-items: center;
        padding: 6px 12px;
        border-radius: 20px;
        font-size: 12px;
        font-weight: 500;
    }
    
    .role-operator {
        background: rgba(59, 130, 246, 0.1);
        color: #3B82F6;
    }
    
    .role-aftersales {
        background: rgba(16, 185, 129, 0.1);
        color: #10B981;
    }
    
    .role-admin {
        background: rgba(139, 92, 246, 0.1);
        color: #8B5CF6;
    }
    
    /* ==================== 加载动画 ==================== */
    .loading-spinner {
        display: inline-block;
        width: 20px;
        height: 20px;
        border: 2px solid #E5E7EB;
        border-top-color: var(--secondary-color);
        border-radius: 50%;
        animation: spin 0.8s linear infinite;
    }
    
    @keyframes spin {
        to { transform: rotate(360deg); }
    }
    
    /* ==================== 模块间距 ==================== */
    .module-spacing {
        margin-bottom: var(--spacing-md);
    }
    </style>
    """

# ===================== 2. 关键词联想配置 =====================
def get_keyword_suggestions():
    """
    获取关键词联想配置
    从现有1034个关键词库中提取
    """
    return {
        "异响": ["发动机异响", "刹车异响", "变速箱异响", "车门异响", "天窗异响", "方向盘异响", "座椅异响", "电机异响", "空调异响"],
        "故障": ["车辆故障", "车机故障", "充电故障", "电池故障", "电机故障", "刹车故障", "转向故障", "空调故障", "灯光故障"],
        "死机": ["车机死机", "屏幕死机", "中控死机", "系统死机", "黑屏死机"],
        "黑屏": ["车机黑屏", "屏幕黑屏", "中控黑屏", "仪表盘黑屏", "显示屏黑屏"],
        "充电": ["充电桩", "充电站", "充电速度", "充电时间", "快充", "慢充", "超充", "充电桩故障", "充电异常"],
        "电池": ["电池衰减", "电池故障", "电池续航", "电池充电", "电池温度", "电池安全", "电池质保"],
        "车机": ["车机卡顿", "车机死机", "车机黑屏", "车机重启", "车机系统", "车机联网", "车机OTA"],
        "服务": ["售后服务", "销售服务", "门店服务", "服务态度", "服务流程", "客服服务"],
        "建议": ["功能建议", "体验建议", "优化建议", "改进建议", "产品建议", "服务建议"],
        "优化": ["系统优化", "功能优化", "体验优化", "车机优化", "APP优化", "充电优化"],
    }

# ===================== 3. 角色配置 =====================
class RoleConfig:
    """角色适配配置"""
    
    ROLES = {
        "operator": {
            "name": "运营岗",
            "modules": ["分类统计", "趋势图表", "数据导出"],
            "color": "#3B82F6"
        },
        "aftersales": {
            "name": "售后岗",
            "modules": ["待处理反馈", "人工复核", "问题追踪"],
            "color": "#10B981"
        },
        "admin": {
            "name": "管理员",
            "modules": ["全部模块"],
            "color": "#8B5CF6"
        }
    }
    
    @staticmethod
    def get_role(key):
        """获取角色配置"""
        return RoleConfig.ROLES.get(key, RoleConfig.ROLES["admin"])
    
    @staticmethod
    def get_all_roles():
        """获取所有角色"""
        return RoleConfig.ROLES

# ===================== 4. 置信度样式 =====================
def get_confidence_style(confidence):
    """
    获取置信度样式
    
    参数:
        confidence: 置信度 (0-1)
    
    返回:
        tuple: (样式类名, 显示文本, 颜色)
    """
    if confidence >= 0.9:
        return ("confidence-high", f"{confidence*100:.0f}%", "#10B981")
    elif confidence >= 0.7:
        return ("confidence-medium", f"{confidence*100:.0f}%", "#F59E0B")
    else:
        return ("confidence-low", f"{confidence*100:.0f}%", "#EF4444")

def render_confidence_badge(confidence):
    """渲染置信度徽章"""
    style, text, color = get_confidence_style(confidence)
    return f'<span class="confidence-badge {style}" style="color:{color}">{text}</span>'

# ===================== 5. 批量操作组件 =====================
class BatchOperationHelper:
    """批量操作辅助类"""
    
    @staticmethod
    def render_batch_actions(selected_count, on_classify, on_review):
        """
        渲染批量操作按钮
        
        参数:
            selected_count: 已选数量
            on_classify: 批量分类回调
            on_review: 批量复核回调
        """
        if selected_count == 0:
            return ""
        
        buttons_html = f"""
        <div class="batch-action-btn" style="margin-top: 12px; display: flex; gap: 8px;">
            <button class="stButton" onclick="{on_classify}">
                批量分类 ({selected_count})
            </button>
            <button class="stButton" onclick="{on_review}">
                批量复核 ({selected_count})
            </button>
        </div>
        """
        return buttons_html

# ===================== 6. 可视化图表配置 =====================
CHART_COLORS = {
    "产品质量问题": "#EF4444",
    "销售服务问题": "#F59E0B",
    "产品咨询": "#3B82F6",
    "体验需求": "#10B981",
    "非问题": "#8B5CF6"
}

def get_chart_config(chart_type="donut"):
    """
    获取图表配置
    
    参数:
        chart_type: 图表类型 (donut/bar/line)
    
    返回:
        dict: ECharts配置
    """
    if chart_type == "donut":
        return {
            "radius": ["40%", "70%"],
            "center": ["50%", "50%"],
            "itemStyle": {
                "borderRadius": 8,
                "borderColor": "#fff",
                "borderWidth": 2
            },
            "label": {
                "show": True,
                "formatter": "{b}: {d}%"
            },
            "emphasis": {
                "itemStyle": {
                    "shadowBlur": 10,
                    "shadowOffsetX": 0,
                    "shadowColor": "rgba(0, 0, 0, 0.5)"
                }
            }
        }
    elif chart_type == "bar":
        return {
            "barWidth": "60%",
            "itemStyle": {
                "borderRadius": [4, 4, 0, 0]
            },
            "label": {
                "show": True,
                "position": "top"
            }
        }
    elif chart_type == "line":
        return {
            "smooth": True,
            "symbol": "circle",
            "symbolSize": 8,
            "lineStyle": {
                "width": 3
            },
            "areaStyle": {
                "opacity": 0.1
            }
        }

# ===================== 7. 移动端适配组件 =====================
def render_mobile_bottom_sheet(content_options, on_select, on_close):
    """
    渲染移动端底部浮层
    
    参数:
        content_options: 选项列表 [{"label": "产品质量问题", "value": "产品质量问题"}, ...]
        on_select: 选择回调
        on_close: 关闭回调
    """
    options_html = ""
    for opt in content_options:
        options_html += f"""
        <div class="keyword-suggestion-item" onclick="{on_select}('{opt['value']}')">
            {opt['label']}
        </div>
        """
    
    return f"""
    <div class="mobile-bottom-sheet" id="mobileSheet">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
            <h4 style="margin:0;">选择分类</h4>
            <button onclick="{on_close}" style="border:none;background:none;font-size:20px;cursor:pointer;">✕</button>
        </div>
        {options_html}
    </div>
    """

# ===================== 8. 角色视图切换 =====================
def render_role_switcher(current_role, on_change):
    """
    渲染角色切换器
    
    参数:
        current_role: 当前角色
        on_change: 切换回调
    """
    roles_html = """
    <div style="display:flex;gap:8px;margin-bottom:16px;">
    """
    
    for key, config in RoleConfig.ROLES.items():
        is_active = "active" if key == current_role else ""
        roles_html += f"""
        <span class="role-badge role-{key} {is_active}" 
              onclick="{on_change}('{key}')"
              style="cursor:pointer;background:{'rgba(59,130,246,0.1)' if key==current_role else 'transparent'};">
            {config['name']}
        </span>
        """
    
    roles_html += "</div>"
    return roles_html

# ===================== 9. 规则配置界面（仅展示）====================
def render_rule_config_panel(keyword_weights):
    """
    渲染规则配置面板（仅前端展示，不修改后端）
    
    参数:
        keyword_weights: 关键词权重配置
    """
    rules = [
        {"name": "故障词", "priority": 1, "keywords": keyword_weights.get("产品质量问题", [])[:10]},
        {"name": "建议词", "priority": 2, "keywords": ["希望", "建议", "优化", "改进", "提升"]},
        {"name": "服务词", "priority": 3, "keywords": ["售后", "服务", "门店", "交付"]},
        {"name": "非问题词", "priority": 4, "keywords": ["感谢", "好评", "表扬"]},
    ]
    
    html = '<div class="rule-config-panel">'
    for rule in rules:
        html += f"""
        <div class="rule-item" style="padding:12px;margin-bottom:8px;background:#F9FAFB;border-radius:8px;">
            <div style="display:flex;justify-content:space-between;align-items:center;">
                <span style="font-weight:600;">优先级{rule['priority']}: {rule['name']}</span>
                <span style="color:#6B7280;font-size:12px;">↕ 拖拽调整</span>
            </div>
            <div style="margin-top:8px;color:#6B7280;font-size:12px;">
                {', '.join(rule['keywords'][:5])}...
            </div>
        </div>
        """
    html += '</div>'
    return html

# ===================== 10. 前端初始化 =====================
def init_frontend():
    """初始化前端配置"""
    st.markdown(get_frontend_styles(), unsafe_allow_html=True)

# ===================== 11. 导出辅助函数 =====================
def export_frontend_config():
    """导出前端配置供主应用使用"""
    return {
        "styles": get_frontend_styles(),
        "suggestions": get_keyword_suggestions(),
        "roles": RoleConfig.ROLES,
        "chart_colors": CHART_COLORS,
        "chart_config": get_chart_config,
        "confidence_style": get_confidence_style,
        "render_confidence_badge": render_confidence_badge,
    }

# ===================== 12. 使用示例 =====================
def demo():
    """演示前端优化组件"""
    st.set_page_config(page_title="前端优化演示", layout="wide")
    
    # 初始化
    init_frontend()
    
    st.title("🚗 前端优化组件演示")
    
    # 1. 置信度徽章
    st.subheader("1. 置信度徽章")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown(render_confidence_badge(0.95), unsafe_allow_html=True)
    with col2:
        st.markdown(render_confidence_badge(0.75), unsafe_allow_html=True)
    with col3:
        st.markdown(render_confidence_badge(0.50), unsafe_allow_html=True)
    
    # 2. 关键词联想
    st.subheader("2. 关键词联想")
    suggestions = get_keyword_suggestions()
    selected = st.selectbox("选择关键词", list(suggestions.keys()))
    if selected:
        st.write("联想词:", suggestions[selected])
    
    # 3. 角色切换
    st.subheader("3. 角色适配")
    st.markdown(render_role_switcher("operator", "console.log"), unsafe_allow_html=True)
    
    # 4. 图表配色
    st.subheader("4. 图表配色")
    for label, color in CHART_COLORS.items():
        st.markdown(f'<div style="display:flex;align-items:center;gap:8px;"><div style="width:20px;height:20px;background:{color};border-radius:4px;"></div>{label}</div>', unsafe_allow_html=True)

if __name__ == "__main__":
    demo()
