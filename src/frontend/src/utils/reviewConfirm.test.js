import { describe, it } from 'node:test'
import assert from 'node:assert/strict'
import {
  fillReviewFromModel,
  confirmAsCorrectError,
  applyConfirmAsCorrect
} from './reviewConfirm.js'

describe('fillReviewFromModel', () => {
  it('空人工标签时采用模型一级/二级', () => {
    const out = fillReviewFromModel(
      { review_l1: '', review_l2: '', review_status: 0 },
      '非问题',
      '咨询与表扬'
    )
    assert.equal(out.review_l1, '非问题')
    assert.equal(out.review_l2, '')
  })

  it('已有人工一级时不覆盖', () => {
    const out = fillReviewFromModel(
      { review_l1: '服务类', review_l2: '售后服务问题', review_status: 0 },
      '非问题',
      ''
    )
    assert.equal(out.review_l1, '服务类')
    assert.equal(out.review_l2, '售后服务问题')
  })

  it('业务类缺二级时用模型二级补齐', () => {
    const out = fillReviewFromModel(
      { review_l1: '产品质量类', review_l2: '', review_status: 0 },
      '产品质量类',
      '座舱问题'
    )
    assert.equal(out.review_l2, '座舱问题')
  })
})

describe('confirmAsCorrectError', () => {
  it('无一级时拒绝', () => {
    assert.ok(confirmAsCorrectError({ review_l1: '', review_l2: '' }))
  })

  it('业务类无二级时拒绝', () => {
    assert.ok(confirmAsCorrectError({ review_l1: '服务类', review_l2: '' }))
  })

  it('非问题不要求二级', () => {
    assert.equal(confirmAsCorrectError({ review_l1: '非问题', review_l2: '' }), '')
  })
})

describe('applyConfirmAsCorrect', () => {
  it('模型正确时标为已复核且不改标签', () => {
    const r = applyConfirmAsCorrect(
      { review_l1: '产品质量类', review_l2: '座舱问题', review_status: 0 },
      '产品质量类',
      '座舱问题'
    )
    assert.equal(r.ok, true)
    assert.equal(r.row.review_status, 1)
    assert.equal(r.row.review_l1, '产品质量类')
    assert.equal(r.row.review_l2, '座舱问题')
  })

  it('不改模型侧字段', () => {
    const src = {
      review_l1: '',
      review_l2: '',
      review_status: 0,
      v3_label_meta: '{"l1":"服务类","l2":"交付问题"}',
      v3_l1: '服务类'
    }
    const r = applyConfirmAsCorrect(src, '服务类', '交付问题')
    assert.equal(r.ok, true)
    assert.equal(r.row.v3_l1, '服务类')
    assert.equal(r.row.v3_label_meta, src.v3_label_meta)
  })

  it('无法补齐时失败且不把状态写成已复核', () => {
    const r = applyConfirmAsCorrect(
      { review_l1: '', review_l2: '', review_status: 0 },
      '',
      ''
    )
    assert.equal(r.ok, false)
    assert.equal(r.row.review_status, 0)
  })
})
