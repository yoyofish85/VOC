import { pinyin } from 'pinyin-pro'

/**
 * 二级标签搜索：原文包含、不区分大小写；支持拼音首字母与全拼（无空格连续匹配）
 */
export function matchL2Label (text, query) {
  const t = String(text || '')
  const qRaw = (query || '').trim()
  if (!qRaw) return true
  const q = qRaw.toLowerCase()
  if (t.toLowerCase().includes(q)) return true
  try {
    const initials = pinyin(t, { pattern: 'first', toneType: 'none', type: 'string' })
      .replace(/\s+/g, '')
      .toLowerCase()
    const fullpy = pinyin(t, { toneType: 'none', type: 'string' })
      .replace(/\s+/g, '')
      .toLowerCase()
    const qn = q.replace(/\s+/g, '')
    return initials.includes(qn) || fullpy.includes(qn)
  } catch {
    return false
  }
}

function escapeHtml (s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

/** 下拉展示：字面量匹配片段高亮（拼音命中整段不高亮，避免误导） */
export function highlightL2Option (text, query) {
  if (!query || !String(query).trim()) return escapeHtml(text)
  const t = String(text || '')
  const q = String(query).trim()
  const lower = t.toLowerCase()
  const qi = lower.indexOf(q.toLowerCase())
  if (qi >= 0) {
    return (
      escapeHtml(t.slice(0, qi)) +
      '<mark class="l2-search-hl">' +
      escapeHtml(t.slice(qi, qi + q.length)) +
      '</mark>' +
      escapeHtml(t.slice(qi + q.length))
    )
  }
  return escapeHtml(t)
}

/** Element Plus filter-method: (query, option) => boolean */
export function rowL2FilterMethod (row) {
  return (query, option) => {
    row._l2FilterQ = query ?? ''
    const v = option?.value ?? option?.label ?? ''
    return matchL2Label(v, query)
  }
}
