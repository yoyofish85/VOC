<template>
  <div class="opinion-review-page">
    <!-- 顶部筛选搜索栏：快速定位舆情，减少比对成本 -->
    <div class="search-filter-bar">
      <el-input
        v-model="searchKey"
        placeholder="搜索舆情ID/原文/来源/模型关键词"
        style="width: 400px;"
        @keyup.enter="getReviewList"
        clearable
      />
      <el-select
        v-model="reviewStatus"
        placeholder="筛选复核状态"
        style="width: 180px; margin-left: 10px;"
        clearable
      >
        <el-option label="未复核" value="0" />
        <el-option label="已复核" value="1" />
        <el-option label="存疑" value="2" />
      </el-select>
      <el-button type="primary" icon="Search" @click="getReviewList" style="margin-left: 10px;">
        查询
      </el-button>
      <el-button icon="Refresh" @click="resetAndGetList" style="margin-left: 10px;">
        刷新
      </el-button>
    </div>

    <!-- 核心复核表格：原文+模型结果同屏展示，行级复核，一键比对 -->
    <el-table
      :data="reviewList"
      border
      stripe
      style="width: 100%; margin-top: 20px;"
      row-key="opinion_id"
      v-loading="loading"
    >
      <!-- 舆情基础标识：快速定位 -->
      <el-table-column prop="opinion_id" label="舆情ID" width="80" align="center" fixed="left" />
      <el-table-column prop="source" label="舆情来源" width="120" align="center" fixed="left" />
      <el-table-column prop="create_time" label="采集时间" width="180" align="center" />

      <!-- 舆情原文：关键词高亮，折叠/展开长文本，方便比对 -->
      <el-table-column label="舆情原文" min-width="400">
        <template #default="scope">
          <div class="text-container">
            <el-collapse-transition>
              <div v-if="scope.row.textExpand" class="text-content">
                <span v-html="highlightKeyword(scope.row.original_text, scope.row.model_keyword)"></span>
              </div>
              <div v-else class="text-content ellipsis">
                <span v-html="highlightKeyword(scope.row.original_text, scope.row.model_keyword)"></span>
              </div>
            </el-collapse-transition>
            <el-button type="text" @click="scope.row.textExpand = !scope.row.textExpand" size="small">
              {{ scope.row.textExpand ? '收起' : '展开' }}
            </el-button>
          </div>
        </template>
      </el-table-column>

      <!-- 模型分类结果：聚合关键词/分类/依据，与原文同屏比对 -->
      <el-table-column label="模型分类结果" min-width="300">
        <template #default="scope">
          <div class="model-result-item">
            <span class="label">分类：</span>
            <span class="content">{{ scope.row.model_class }}</span>
          </div>
          <div class="model-result-item">
            <span class="label">关键词：</span>
            <span class="content keyword">{{ scope.row.model_keyword }}</span>
          </div>
          <div class="model-result-item">
            <span class="label">判定依据：</span>
            <span class="content reason">{{ scope.row.model_reason || '无' }}</span>
          </div>
        </template>
      </el-table-column>

      <!-- 人工复核：行级独立操作，绑定原数据对象，解决回弹问题 -->
      <el-table-column label="人工复核" min-width="280" fixed="right" align="center">
        <template #default="scope">
          <div class="review-operate">
            <!-- 复核状态选择 -->
            <el-select
              v-model="scope.row.review_status"
              placeholder="选择复核状态"
              style="width: 120px; margin-bottom: 8px;"
              @change="handleStatusChange(scope.row)"
            >
              <el-option label="未复核" value="0" />
              <el-option label="已复核" value="1" />
              <el-option label="存疑" value="2" />
            </el-select>

            <!-- 复核结果输入：仅已复核状态显示，绑定原对象 -->
            <el-input
              v-if="scope.row.review_status === 1"
              v-model="scope.row.review_result"
              placeholder="请输入人工复核结果"
              style="width: 220px; margin-bottom: 8px;"
              clearable
            />

            <!-- 复核备注（可选） -->
            <el-input
              v-model="scope.row.review_note"
              placeholder="复核备注（可选）"
              style="width: 220px; margin-bottom: 8px;"
              clearable
              size="small"
            />

            <!-- 保存按钮：单条独立保存，异步等待结果，避免回弹 -->
            <el-button
              type="primary"
              size="small"
              icon="Save"
              @click="saveReview(scope.row)"
            >
              保存
            </el-button>
          </div>
        </template>
      </el-table-column>
    </el-table>

    <!-- 分页组件：配合列表查询 -->
    <el-pagination
      @size-change="handleSizeChange"
      @current-change="handleCurrentChange"
      :current-page="page"
      :page-sizes="[10, 20, 50, 100]"
      :page-size="size"
      layout="total, sizes, prev, pager, next, jumper"
      :total="total"
      style="margin-top: 20px; text-align: right;"
    />
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getReviewListApi, saveReviewApi } from '@/api/review'

// 分页参数
const page = ref(1)
const size = ref(20)
const total = ref(0)
// 筛选参数
const searchKey = ref('')
const reviewStatus = ref(null)
// 列表数据+加载状态
const reviewList = ref([])
const loading = ref(false)

// 页面初始化：获取复核列表
onMounted(() => {
  getReviewList()
})

// 获取复核列表：封装参数，统一调用
const getReviewList = async () => {
  loading.value = true
  try {
    const res = await getReviewListApi({
      page: page.value,
      size: size.value,
      searchKey: searchKey.value,
      reviewStatus: reviewStatus.value
    })
    if (res.code === 200) {
      reviewList.value = res.data  // 绑定原数据对象，不做深拷贝，解决回弹核心
      total.value = res.total
    } else {
      ElMessage.error(res.msg || '获取复核列表失败')
    }
  } catch (e) {
    ElMessage.error('获取数据异常，请刷新重试')
  } finally {
    loading.value = false
  }
}

// 关键词高亮：模型提取的关键词在原文中高亮，方便比对
const highlightKeyword = (text, keyword) => {
  if (!text || !keyword) return text
  // 处理多关键词（逗号分隔）
  const keywordList = keyword.split(',').map(k => k.trim()).filter(k => k)
  if (keywordList.length === 0) return text
  // 正则匹配关键词，添加高亮样式
  const reg = new RegExp(`(${keywordList.join('|')})`, 'g')
  return text.replace(reg, '<span class="keyword-highlight">$1</span>')
}

// 复核状态变化：存疑/未复核时清空复核结果，避免脏数据
const handleStatusChange = (row) => {
  if (row.review_status !== 1) {
    row.review_result = null
  }
}

// 保存复核结果：核心解决回弹问题，异步等待接口响应，失败保留输入值
const saveReview = async (row) => {
  // 前端二次校验：已复核状态必须输入结果
  if (row.review_status === 1 && (!row.review_result || row.review_result.trim() === '')) {
    ElMessage.warning('已复核状态下，复核结果不能为空')
    return
  }
  // 缓存当前输入值：接口失败时恢复，避免回弹
  const tempData = { ...row }
  try {
    const res = await saveReviewApi({
      opinion_id: row.opinion_id,
      review_status: row.review_status,
      review_result: row.review_result,
      review_note: row.review_note
    })
    if (res.code === 200) {
      ElMessage.success(res.msg)
      getReviewList()  // 仅成功后刷新列表，确保获取最新数据
    } else {
      ElMessage.error(res.msg || '保存失败')
      // 接口失败：恢复缓存的输入值，避免回弹
      Object.assign(row, tempData)
    }
  } catch (e) {
    ElMessage.error('网络异常，保存失败')
    // 网络异常：恢复缓存的输入值，避免回弹
    Object.assign(row, tempData)
  }
}

// 分页事件
const handleSizeChange = (val) => {
  size.value = val
  getReviewList()
}
const handleCurrentChange = (val) => {
  page.value = val
  getReviewList()
}

// 重置筛选条件并刷新
const resetAndGetList = () => {
  searchKey.value = ''
  reviewStatus.value = null
  page.value = 1
  getReviewList()
}
</script>

<style scoped>
.opinion-review-page {
  width: 100%;
  height: 100%;
}
.search-filter-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
}
.text-container {
  width: 100%;
}
.text-content {
  line-height: 1.6;
  word-break: break-all;
}
.ellipsis {
  overflow: hidden;
  text-overflow: -o-ellipsis-lastline;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
}
.model-result-item {
  line-height: 1.5;
  margin-bottom: 4px;
  word-break: break-all;
}
.model-result-item .label {
  color: #666;
  font-size: 12px;
  font-weight: 500;
}
.model-result-item .content {
  color: #333;
  font-size: 12px;
}
.model-result-item .keyword {
  color: #e6a23c;
  font-weight: 500;
}
.model-result-item .reason {
  color: #999;
}
.review-operate {
  display: flex;
  flex-direction: column;
  align-items: center;
}
/* 关键词高亮样式 */
:deep(.keyword-highlight) {
  color: #e6a23c;
  font-weight: 600;
  background: #fffbe6;
  padding: 0 2px;
  border-radius: 2px;
}
</style>