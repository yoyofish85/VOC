/**
 * 人工复核「确认正确」：采用模型标签（仅当人工为空）并标为已复核。
 * 不修改 v3_label_meta / 模型分类结果。
 */

export const NON_ISSUE_L1 = '非问题'

export function isNonIssueL1 (l1) {
  return (l1 || '').trim() === NON_ISSUE_L1
}

export function isL2RequiredForL1 (l1) {
  return !isNonIssueL1(l1)
}

export function fillReviewFromModel (row, modelL1, modelL2) {
  const next = { ...row }
  if (!(next.review_l1 || '').trim() && (modelL1 || '').trim()) {
    next.review_l1 = String(modelL1).trim()
  }
  const l1 = (next.review_l1 || '').trim()
  if (isNonIssueL1(l1)) {
    next.review_l2 = ''
  } else if (!(next.review_l2 || '').trim() && (modelL2 || '').trim()) {
    next.review_l2 = String(modelL2).trim()
  }
  return next
}

export function confirmAsCorrectError (row) {
  const l1 = (row?.review_l1 || '').trim()
  if (!l1) return '请先确认一级标签（模型无一级时需手工填写）'
  if (isL2RequiredForL1(l1) && !(row?.review_l2 || '').trim()) {
    return '业务类须填写二级标签'
  }
  return ''
}

export function applyConfirmAsCorrect (row, modelL1, modelL2) {
  const filled = fillReviewFromModel(row, modelL1, modelL2)
  const error = confirmAsCorrectError(filled)
  if (error) {
    return { ok: false, error, row: filled }
  }
  return {
    ok: true,
    error: '',
    row: { ...filled, review_status: 1 }
  }
}
