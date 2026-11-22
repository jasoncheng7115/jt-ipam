<template>
  <div class="cip-page">
    <n-spin :show="loading && !plan">
      <n-card v-if="plan" :bordered="false" content-style="padding: 14px 16px">
        <div class="cip-head">
          <div class="cip-head__title">
            <n-icon :size="20"><ChangeImpactIcon /></n-icon>
            <span data-testid="cip-plan-title">{{ plan.title }}</span>
            <n-tag size="small" :type="lifecycleType(plan.lifecycle)" data-testid="cip-lifecycle">
              {{ t(`change_impact.lifecycle.${plan.lifecycle}`) }}
            </n-tag>
          </div>
          <!-- 排法與顏色照 IP 頁、裝置頁：每顆都有圖示；推進流程的動作（送審、覆核…）藍色、編輯綠色、
               取消計畫紅框（取消後不能復原，要先確認）、返回放最右邊（使用者 2026-10-07） -->
          <n-space :size="8" :wrap="true">
            <n-button v-for="a in forwardActions" :key="a" type="info" size="small"
                      :loading="busy === a" :data-testid="`cip-act-${a}`" @click="act(a)">
              <template #icon><n-icon><component :is="ACT_ICONS[a]" /></n-icon></template>{{ t(`change_impact.act.${a}`) }}
            </n-button>
            <n-button v-if="canRun" size="small" :loading="busy === 'run'" data-testid="cip-rerun" @click="rerun">
              <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("change_impact.rerun") }}
            </n-button>
            <!-- 已取消的也可以重新分析：先恢復成草稿（使用者 2026-10-08） -->
            <n-popconfirm v-if="canReopen" @positive-click="reopenAndRun">
              <template #trigger>
                <n-button size="small" :loading="busy === 'run'" data-testid="cip-reopen-rerun">
                  <template #icon><n-icon><RefreshIcon /></n-icon></template>{{ t("change_impact.rerun") }}
                </n-button>
              </template>
              {{ t("change_impact.reopen_confirm") }}
            </n-popconfirm>
            <n-dropdown v-if="run && isDone" :options="exportOptions" :disabled="exporting" @select="(k: string) => runExport(() => doExport(k))">
              <n-button size="small" :loading="exporting" :disabled="exporting" data-testid="cip-export"><template #icon><n-icon><DownloadIcon /></n-icon></template>{{ t("change_impact.export") }}</n-button>
            </n-dropdown>
            <n-button v-if="plan.can_edit && editable" type="primary" size="small" @click="openEdit">
              <template #icon><n-icon><EditIcon /></n-icon></template>{{ t("common.edit") }}
            </n-button>
            <n-popconfirm v-if="actions.includes('cancel')" @positive-click="act('cancel')">
              <template #trigger>
                <n-button type="error" ghost size="small" :loading="busy === 'cancel'" data-testid="cip-act-cancel">
                  <template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("change_impact.act.cancel") }}
                </n-button>
              </template>
              {{ t("change_impact.cancel_confirm") }}
            </n-popconfirm>
            <n-button size="small" @click="router.push({ name: 'change_impact' })">
              <template #icon><n-icon><ArrowLeftIcon /></n-icon></template>{{ t("common.back") }}
            </n-button>
          </n-space>
        </div>
        <div class="cip-meta">
          <span>{{ t(`change_impact.scenario.${plan.scenario_type}`) }}</span>
          <span class="cip-mono">{{ plan.target_label }}<template v-if="plan.parameters.new_ip"> → {{ plan.parameters.new_ip }}</template></span>
          <span v-if="plan.planned_start">{{ t("change_impact.window_label") }} {{ fmtDateTime(plan.planned_start) }}<template v-if="plan.planned_end"> – {{ fmtDateTime(plan.planned_end) }}</template></span>
          <span v-if="plan.parameters.mode">{{ t(`change_impact.param.mode.${plan.parameters.mode}`) }}</span>
          <span v-if="plan.also_down_labels?.length" data-testid="cip-also-down">
            {{ t("change_impact.also_down_label", { names: plan.also_down_labels.join("、") }) }}
          </span>
          <span>{{ t("change_impact.revision", { n: plan.revision }) }}</span>
          <!-- 送審中：送給誰（有名單是名單裡看得到目標的人，沒名單是管理員） -->
          <span v-if="plan.lifecycle === 'in_review'" data-testid="cip-reviewers">
            {{ t("change_impact.reviewers_label") }}：{{ reviewersText }}
          </span>
        </div>
        <!-- 多關卡審核的進度（「申請審核設定」設成會簽或依序多關卡時） -->
        <div v-if="plan.review_steps?.length && ['in_review', 'approved'].includes(plan.lifecycle)" class="cip-steps"
             data-testid="cip-review-steps">
          <span class="cip-muted">{{ t(plan.review_mode === "stages" ? "change_impact.steps_stages" : "change_impact.steps_parallel") }}</span>
          <n-tag v-for="st in plan.review_steps" :key="st.index" size="small" :bordered="false"
                 :type="st.approved ? 'success' : st.is_current ? 'info' : 'default'"
                 :title="st.approvers.join('、')">
            {{ st.index + 1 }}. {{ st.name }}：{{ t(st.approved ? "change_impact.step_done" : st.is_current ? "change_impact.step_current" : "change_impact.step_waiting") }}
          </n-tag>
        </div>
        <n-alert v-if="plan.archived_at" type="info" :bordered="false" style="margin-top: 10px" data-testid="cip-archived">
          {{ t("change_impact.archived_notice", { at: fmtDateTime(plan.archived_at) }) }}
        </n-alert>
        <!-- 被退回或駁回：誰、什麼時候、為什麼，放在最上面（2026-10-25 實測：理由只在歷史頁籤，申請人找不到） -->
        <n-alert v-if="returnedReview" :type="returnedReview.decision === 'reject' ? 'error' : 'warning'" :bordered="false"
                 style="margin-top: 10px" data-testid="cip-returned">
          <template #header>
            {{ t(`change_impact.returned_${returnedReview.decision}`, { user: returnedReview.reviewer_name || "—",
                                                                       at: fmtDateTime(returnedReview.created_at) }) }}
          </template>
          <span class="cip-wrap">{{ returnedReview.rationale }}</span>
        </n-alert>
        <div class="cip-dryrun" data-testid="cip-dryrun">{{ t("change_impact.dry_run_notice") }}</div>
      </n-card>
    </n-spin>
    <n-alert v-if="error" type="error" :bordered="false">{{ error }}</n-alert>

    <!-- 分析狀態與摘要 -->
    <n-card v-if="plan" size="small">
      <div v-if="!run" class="cip-muted">{{ t("change_impact.never_run") }}</div>
      <div v-else-if="!isDone" class="cip-running" data-testid="cip-running">
        <n-spin v-if="isActive" :size="14" />
        <span>{{ t(`change_impact.job.${run.job_status}`) }}</span>
        <span v-if="run.error_code" class="cip-err">{{ errText(run.error_code) }}</span>
        <n-button v-if="isActive" size="tiny" secondary @click="cancel">
          <template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("change_impact.cancel_run") }}
        </n-button>
      </div>
      <template v-else>
        <div class="cip-summary" data-testid="cip-summary">
          <div class="cip-stat">
            <div class="cip-stat__k">{{ t("change_impact.col_decision") }}</div>
            <n-tag :type="decisionType(run.decision_status)" data-testid="cip-decision">
              {{ t(`change_impact.decision.${run.decision_status}`) }}
            </n-tag>
          </div>
          <div class="cip-stat">
            <div class="cip-stat__k">{{ t("change_impact.col_completeness") }}</div>
            <span :class="run.completeness === 'complete' ? '' : 'cip-warn'" data-testid="cip-completeness">
              {{ t(`change_impact.completeness.${run.completeness}`) }}
            </span>
          </div>
          <!-- 「需變更」是「需覆核」的一部分（使用者看到 41 與 40 並排，以為是兩群不同的項目）→ 寫在需覆核底下 -->
          <div v-for="k in ['blockers', 'review', 'gaps']" :key="k" class="cip-stat"
               :class="{ 'cip-stat--bad': k === 'blockers' && (run.counts?.blockers ?? 0) > 0 }">
            <div class="cip-stat__k">{{ t(`change_impact.count.${k}`) }}</div>
            <div class="cip-stat__v">{{ run.counts ? (run.counts[k] ?? 0) : "—" }}</div>
            <div v-if="k === 'review' && run.counts?.change_required" class="cip-muted" data-testid="cip-change-required">
              {{ t("change_impact.count.of_which_change", { n: run.counts.change_required }) }}
            </div>
          </div>
          <div class="cip-stat cip-stat--time">
            <div class="cip-stat__k">{{ t("change_impact.analyzed_at") }}</div>
            <div>{{ fmtDateTime(run.completed_at) }}</div>
            <div class="cip-muted" :class="{ 'cip-warn': expired }">
              {{ expired ? t("change_impact.expired") : t("change_impact.valid_until", { at: fmtDateTime(run.expires_at) }) }}
            </div>
          </div>
        </div>
        <n-alert v-if="run.permission_scope_changed" type="warning" :bordered="false" style="margin-top: 10px">
          {{ t("change_impact.scope_changed") }}
        </n-alert>
        <n-alert v-if="run.truncated && run.truncation" type="warning" :bordered="false" style="margin-top: 10px">
          {{ t("change_impact.truncated", { what: run.truncation.what, limit: run.truncation.limit }) }}
        </n-alert>
        <div class="cip-scope-note">{{ t("change_impact.scope_note") }}</div>
      </template>
    </n-card>

    <n-card v-if="plan && run && isDone" size="small">
      <n-tabs v-model:value="tab" type="line" animated>
        <!-- 影響清單 -->
        <n-tab-pane name="findings" :tab="t('change_impact.tab_findings', { n: findings.length })">
          <div class="cip-filters">
            <n-select v-model:value="fDisp" :options="dispOptions" clearable size="small" style="width: 150px"
                      :placeholder="t('change_impact.col_disposition')" />
            <n-select v-model:value="fCat" :options="catOptions" clearable size="small" style="width: 150px"
                      :placeholder="t('change_impact.col_category')" />
          </div>
          <div v-if="!findings.length" class="cip-empty" data-testid="cip-zero">{{ t("change_impact.zero_findings") }}</div>
          <n-data-table v-else :columns="findingCols" :data="shownFindings" size="small" :bordered="false"
                        :row-key="(r: ImpactFinding) => r.id" :scroll-x="1040" data-testid="cip-findings"
                        :expanded-row-keys="openRows" @update:expanded-row-keys="onExpandRows"
                        :row-class-name="(r: ImpactFinding) => openRows.includes(r.id) ? 'cip-row-open' : ''" />
        </n-tab-pane>

        <!-- 證據與資料缺口 -->
        <!-- 關係圖：只在有結果時才畫（切到這個分頁才載入） -->
        <n-tab-pane name="graph" :tab="t('change_impact.tab_graph')" display-directive="if">
          <ImpactRelationGraph v-if="run && isDone" :run-id="run.id" />
        </n-tab-pane>
        <n-tab-pane name="evidence" :tab="t('change_impact.tab_evidence', { n: gaps.length })">
          <div class="cip-section-k">{{ t("change_impact.gaps_title") }}</div>
          <!-- 表格：每一項之間有分隔線、斑馬紋（使用者 2026-10-08：每一項要有分格線，或做成表格） -->
          <table v-if="gaps.length" class="cip-gt" data-testid="cip-gaps">
            <thead>
              <tr>
                <th style="width: 110px">{{ t("change_impact.gap_col_category") }}</th>
                <th>{{ t("change_impact.gap_col_text") }}</th>
                <th style="width: 160px">{{ t("change_impact.gap_col_affected") }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="g in gaps" :key="g.id">
                <td><n-tag size="small" :bordered="false">{{ catLabel(g.category) }}</n-tag></td>
                <td>{{ gapText(t, te, g.reason_code, g.params) }}</td>
                <td class="cip-muted">{{ gapAffected(t, te, g) || "—" }}</td>
              </tr>
            </tbody>
          </table>
          <div v-else class="cip-muted" data-testid="cip-gaps">{{ t("change_impact.no_gaps") }}</div>
          <div class="cip-section-k" style="margin-top: 14px">{{ t("change_impact.evidence_title") }}</div>
          <n-data-table :columns="evidenceCols" :data="evidence" size="small" :bordered="false"
                        :row-key="(r: ImpactEvidence) => r.id" :scroll-x="820" data-testid="cip-evidence" />
          <div class="cip-section-k" style="margin-top: 14px">{{ t("change_impact.sources_title") }}</div>
          <!-- 用到的來源：一個來源一枚，名稱＋種類，前面的點是資料新鮮度（使用者 10-21：原本一串括號太難看） -->
          <div class="cip-sources" data-testid="cip-sources">
            <span v-for="s in sortedSources" :key="s.kind + s.name" class="cip-source"
                  :title="s.last_sync_at ? `${t('change_impact.last_sync')} ${fmtDateTime(s.last_sync_at)}` : undefined">
              <i class="cip-source__dot" :class="`fr-${freshTone(s.freshness)}`" />
              <span class="cip-source__name">{{ s.name }}</span>
              <span class="cip-source__kind">{{ kindLabel(s.kind) }}</span>
              <span v-if="freshTone(s.freshness) !== 'ok'" class="cip-source__fr" :class="`fr-${freshTone(s.freshness)}`">
                {{ te(`change_impact.fresh.${s.freshness}`) ? t(`change_impact.fresh.${s.freshness}`) : s.freshness }}
              </span>
            </span>
            <span v-if="!sortedSources.length" class="cip-muted">—</span>
          </div>
        </n-tab-pane>

        <!-- 待辦與復原 -->
        <n-tab-pane name="tasks" :tab="t('change_impact.tab_tasks', { n: tasks.length })">
          <div class="cip-manual-note">{{ t("change_impact.manual_note") }}</div>
          <!-- 依階段分區（使用者 2026-10-08：前置、變更、驗證、復原要視覺上容易識別）：淡色標題列＋標題＋數量＋說明 -->
          <div v-for="ph in phases" :key="ph" :class="['cip-phase', `cip-phase--${ph}`]" :data-testid="`cip-phase-${ph}`">
            <div class="cip-phase__head">
              <span class="cip-phase__name">{{ t(`change_impact.phase.${ph}`) }}</span>
              <span class="cip-phase__count">{{ tasksOf(ph).length }}</span>
              <span class="cip-phase__desc">{{ t(`change_impact.phase_desc.${ph}`) }}</span>
            </div>
            <div v-for="tk in tasksOf(ph)" :key="tk.id" class="cip-task" data-testid="cip-task">
              <n-select :value="tk.state" size="tiny" style="width: 110px" :options="taskStateOptions"
                        :disabled="!editable" @update:value="(v: string) => setTaskState(tk, v)" />
              <div class="cip-task__body">
                <div :class="{ 'cip-done': tk.state === 'done' || tk.state === 'skipped' }">{{ taskTitle(t, te, tk) }}</div>
                <div v-if="tk.completion_note" class="cip-muted">{{ tk.completion_note }}</div>
              </div>
              <n-tag v-if="tk.origin !== 'rule_template'" size="tiny" :bordered="false">{{ t(`change_impact.origin.${tk.origin}`) }}</n-tag>
            </div>
          </div>
          <n-button v-if="editable && plan.can_edit" size="small" style="margin-top: 8px" @click="newTask = { phase: 'change', title: '' }">
            <template #icon><n-icon><PlusIcon /></n-icon></template>{{ t("change_impact.add_task") }}
          </n-button>
        </n-tab-pane>

        <!-- AI 解說 -->
        <n-tab-pane name="ai" :tab="t('change_impact.tab_ai')">
          <div v-if="!settings.ai_available" class="cip-muted">{{ t("errors.impact_ai_unavailable") }}</div>
          <template v-else>
            <n-space :size="8" style="margin-bottom: 10px">
              <n-button v-for="k in ['summary', 'checklist', 'explanation']" :key="k" size="small" :loading="busy === `ai-${k}`"
                        :data-testid="`cip-ai-${k}`" @click="genAi(k)">
                <template #icon><n-icon><AiIcon /></n-icon></template>{{ t(`change_impact.ai_gen.${k}`) }}
              </n-button>
            </n-space>
            <div class="cip-ask">
              <n-input v-model:value="question" size="small" :placeholder="t('change_impact.ask_ph')" maxlength="1000"
                       @keyup.enter="ask" />
              <n-button size="small" type="primary" :disabled="!question.trim()" :loading="busy === 'ask'" @click="ask">
                <template #icon><n-icon><SendIcon /></n-icon></template>{{ t("change_impact.ask") }}
              </n-button>
            </div>
          </template>
          <div class="cip-ai-note">{{ t("change_impact.ai_note") }}</div>
          <div v-for="a in artifacts" :key="a.id" class="cip-ai" data-testid="cip-ai-item">
            <div class="cip-ai__head">
              <strong>{{ t(`change_impact.ai_type.${a.artifact_type}`) }}</strong>
              <span v-if="a.question" class="cip-muted">「{{ a.question }}」</span>
              <template v-if="a.status === 'pending' || a.status === 'running'">
                <n-spin :size="12" />
                <!-- 只有轉圈看不出在做什麼（使用者 2026-10-08）：顯示做到哪一步與經過秒數 -->
                <span class="cip-ai__stage" data-testid="cip-ai-stage">{{ aiStageText(a) }}</span>
              </template>
              <n-tag v-if="a.status === 'fallback'" size="tiny" type="warning" :bordered="false">{{ t("change_impact.ai_fallback") }}</n-tag>
              <span class="cip-muted">{{ a.model ?? "" }} {{ a.generated_at ? fmtDateTime(a.generated_at) : "" }}</span>
            </div>
            <div v-if="a.status === 'fallback' && a.error_code" class="cip-muted cip-ai__why" data-testid="cip-ai-why">
              {{ t("change_impact.ai_fallback_why", { reason: errText(a.error_code) }) }}</div>
            <div v-if="a.hidden_scope_changed" class="cip-muted">{{ t("change_impact.ai_hidden") }}</div>
            <template v-else-if="a.output">
              <ul class="cip-ai__list">
                <li v-for="(it, i) in a.output.summary ?? []" :key="'s' + i">
                  {{ aiText(it) }}
                  <!-- 引用不只編號：附上物件名稱，滑過看原因；多的先收起來（使用者 2026-10-08） -->
                  <div v-if="(it.finding_ids ?? []).length" class="cip-cites">
                    <a v-for="fid in shownCites(a.id, i, it.finding_ids ?? [])" :key="fid" class="cip-cite"
                       :title="citeReason(fid)" @click="focusFinding(fid)">#{{ shortRef(fid) }} {{ citeLabel(fid) }}</a>
                    <a v-if="(it.finding_ids ?? []).length > CITES_SHOWN" class="cip-cite cip-cite--more"
                       @click="toggleCites(a.id, i)">{{ citesOpen[`${a.id}:${i}`]
                         ? t("change_impact.ai_cites_less")
                         : t("change_impact.ai_cites_more", { n: (it.finding_ids ?? []).length - CITES_SHOWN }) }}</a>
                  </div>
                </li>
              </ul>
              <div v-if="(a.output.uncertainties ?? []).length" class="cip-section-k">{{ t("change_impact.ai_uncertain") }}</div>
              <ul class="cip-ai__list">
                <li v-for="(it, i) in a.output.uncertainties ?? []" :key="'u' + i">{{ aiText(it) }}</li>
              </ul>
              <template v-if="(a.output.suggested_tasks ?? []).length && a.status === 'completed'">
                <div class="cip-section-k">{{ t("change_impact.ai_tasks") }}</div>
                <n-checkbox-group v-model:value="picked[a.id]">
                  <div v-for="(it, i) in a.output.suggested_tasks ?? []" :key="'t' + i" class="cip-ai-task">
                    <n-checkbox :value="i" :disabled="!plan.can_edit" />
                    <span class="cip-muted">{{ t(`change_impact.phase.${it.phase}`) }}</span>
                    <span>{{ it.text }}</span>
                  </div>
                </n-checkbox-group>
                <n-button v-if="plan.can_edit" size="tiny" :disabled="!(picked[a.id] ?? []).length" style="margin-top: 6px"
                          type="primary" @click="acceptDrafts(a.id)">
                  <template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("change_impact.ai_accept") }}
                </n-button>
              </template>
            </template>
          </div>
        </n-tab-pane>

        <!-- 歷史 -->
        <n-tab-pane name="history" :tab="t('change_impact.tab_history')">
          <!-- 三段都排成表格：同一欄對齊（使用者 2026-10-08） -->
          <div class="cip-section-k">{{ t("change_impact.runs_title") }}</div>
          <div class="cip-hist cip-hist--runs" data-testid="cip-hist-runs">
            <div v-for="r in runs" :key="r.id" class="cip-hist__row">
              <span>{{ fmtDateTime(r.created_at) }}</span>
              <span>{{ t("change_impact.revision", { n: r.plan_revision }) }}</span>
              <span>{{ t(`change_impact.job.${r.job_status}`) }}</span>
              <span><n-tag v-if="r.decision_status" size="tiny" :type="decisionType(r.decision_status)">{{ t(`change_impact.decision.${r.decision_status}`) }}</n-tag></span>
              <span>
                <a v-if="r.id !== run.id && ['completed', 'partial'].includes(r.job_status)" @click="pickRun(r.id)">{{ t("change_impact.view") }}</a>
                <span v-else-if="r.id === run.id" class="cip-muted">{{ t("change_impact.viewing") }}</span>
              </span>
            </div>
          </div>
          <div class="cip-section-k" style="margin-top: 14px">{{ t("change_impact.reviews_title") }}</div>
          <div v-if="reviews.length" class="cip-hist cip-hist--reviews">
            <div v-for="rv in reviews" :key="rv.id" class="cip-hist__row">
              <span>{{ fmtDateTime(rv.created_at) }}</span>
              <span>{{ t(`change_impact.review_decision.${rv.decision}`) }}</span>
              <span>{{ rv.reviewer_name || "—" }}</span>
              <span>{{ t("change_impact.revision", { n: rv.revision }) }}</span>
              <span class="cip-muted cip-wrap">{{ rv.rationale }}</span>
            </div>
          </div>
          <div v-else class="cip-muted">—</div>
          <div class="cip-section-k" style="margin-top: 14px">{{ t("change_impact.revisions_title") }}</div>
          <div class="cip-hist cip-hist--revisions">
            <div v-for="rv in revisions" :key="rv.revision" class="cip-hist__row">
              <span>{{ t("change_impact.revision", { n: rv.revision }) }}</span>
              <span>{{ fmtDateTime(rv.created_at) }}</span>
              <span class="cip-mono cip-muted">{{ rv.payload?.parameters?.new_ip ?? "" }}</span>
            </div>
          </div>
        </n-tab-pane>
      </n-tabs>
    </n-card>

    <!-- 覆核 -->
    <n-modal v-model:show="reviewOpen" preset="card" :title="t('change_impact.act.review')" style="width: min(720px, 96vw)">
      <!-- 有我看不到的需覆核項目：不能替它核准，先講明，核准類選項停用（2026-10-25 實測：以前只回「還有 N 項沒處置」） -->
      <!-- 有阻擋項目不能核准（伺服器也會擋）：先講明，不要讓審核人逐項選完處置才被退（2026-10-25 實測） -->
      <n-alert v-if="run?.decision_status === 'blocked'" type="error" :bordered="false" style="margin-bottom: 10px"
               data-testid="cip-review-blocked">
        {{ t("change_impact.review_blocked") }}
      </n-alert>
      <n-alert v-else-if="run?.review_items_hidden" type="warning" :bordered="false" style="margin-bottom: 10px"
               data-testid="cip-review-hidden">
        {{ t("change_impact.review_hidden") }}
      </n-alert>
      <n-radio-group v-model:value="review.decision" name="decision" style="margin-bottom: 10px">
        <n-radio-button v-for="d in ['approve', 'accept_risk', 'request_changes', 'reject']" :key="d" :value="d"
                        :disabled="!canApprove && (d === 'approve' || d === 'accept_risk')">
          {{ t(`change_impact.review_decision.${d}`) }}
        </n-radio-button>
      </n-radio-group>
      <div v-if="review.decision === 'approve' || review.decision === 'accept_risk'">
        <div class="cip-section-k">{{ t("change_impact.dispositions_title", { n: reviewFindings.length }) }}</div>
        <div v-for="f in reviewFindings" :key="f.id" class="cip-disp">
          <div class="cip-disp__label">{{ f.subject_label }}<div class="cip-muted">{{ reasonText(t, te, f.reason_code, f.params) }}</div></div>
          <n-select v-model:value="review.disp[f.id]" size="small" style="width: 170px" :options="dispActionOptions"
                    :placeholder="t('change_impact.pick_action')" />
        </div>
      </div>
      <n-input v-model:value="review.rationale" type="textarea" :rows="3" :placeholder="t('change_impact.rationale_ph')"
               style="margin-top: 10px" />
      <template #footer>
        <n-space justify="end">
          <n-button @click="reviewOpen = false">
            <template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.cancel") }}
          </n-button>
          <n-button type="primary" :loading="busy === 'review'" data-testid="cip-review-submit" @click="submitReview">
            <template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.confirm") }}
          </n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- 送審前確認：講清楚會通知誰（使用者 2026-10-08），確認才真的送出 -->
    <n-modal v-model:show="submitConfirmOpen" preset="card" :title="t('change_impact.submit_confirm_title')"
             style="width: min(520px, 96vw)" data-testid="cip-submit-confirm">
      <p class="cip-submit-lead">{{ submitLead }}</p>
      <n-space v-if="plan?.reviewers?.length" :size="6" style="margin-bottom: 6px" data-testid="cip-submit-reviewers">
        <n-tag v-for="r in plan.reviewers" :key="r.id" size="small" :bordered="false">{{ r.name }}</n-tag>
        <span v-if="(plan.reviewers_total ?? 0) > plan.reviewers.length" class="cip-muted">
          {{ t("change_impact.reviewers_more", { n: plan.reviewers_total }) }}
        </span>
      </n-space>
      <n-alert v-if="plan?.reviewers_designated && !plan?.reviewers?.length" type="warning" :bordered="false">
        {{ t("change_impact.submit_confirm_nobody") }}
      </n-alert>
      <template #footer>
        <n-space justify="end">
          <n-button @click="submitConfirmOpen = false">
            <template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.cancel") }}
          </n-button>
          <n-button type="info" :loading="busy === 'submit'" data-testid="cip-submit-confirm-ok" @click="act('submit')">
            <template #icon><n-icon><SendIcon /></n-icon></template>{{ t("change_impact.submit_confirm_ok") }}
          </n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- 編輯（產生新版本） -->
    <n-modal v-model:show="editOpen" preset="card" :title="t('common.edit')" style="width: min(520px, 96vw)">
      <n-form label-placement="top">
        <n-form-item :label="t('change_impact.f_title')"><n-input v-model:value="edit.title" maxlength="200" /></n-form-item>
        <n-form-item v-if="plan?.scenario_type === 'ip_renumber'" :label="t('change_impact.f_new_ip')">
          <n-input v-model:value="edit.new_ip" />
        </n-form-item>
      </n-form>
      <div class="cip-muted">{{ t("change_impact.edit_note") }}</div>
      <template #footer>
        <n-space justify="end">
          <n-button @click="editOpen = false">
            <template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.cancel") }}
          </n-button>
          <n-button type="primary" :loading="busy === 'edit'" @click="saveEdit">
            <template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.save") }}
          </n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- 新增手動待辦 -->
    <n-modal :show="!!newTask" preset="card" :title="t('change_impact.add_task')" style="width: min(480px, 96vw)"
             @update:show="(v: boolean) => { if (!v) newTask = null }">
      <n-form v-if="newTask" label-placement="top">
        <n-form-item :label="t('change_impact.col_phase')">
          <n-select v-model:value="newTask.phase" :options="phases.map((p) => ({ label: t(`change_impact.phase.${p}`), value: p }))" />
        </n-form-item>
        <n-form-item :label="t('change_impact.f_title')"><n-input v-model:value="newTask.title" maxlength="300" /></n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="newTask = null">
            <template #icon><n-icon><CancelIcon /></n-icon></template>{{ t("common.cancel") }}
          </n-button>
          <n-button type="primary" :disabled="!newTask?.title.trim()" @click="saveTask">
            <template #icon><n-icon><SaveIcon /></n-icon></template>{{ t("common.save") }}
          </n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>

<script setup lang="ts">
import { type Component, computed, h, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import {
  NAlert, NButton, NCard, NCheckbox, NCheckboxGroup, NDataTable, NDropdown, NForm, NFormItem, NIcon, NInput, NModal, NPopconfirm,
  NRadioButton, NRadioGroup, NSelect, NSpace, NSpin, NTabPane, NTabs, NTag, type DataTableColumns, useMessage,
} from "naive-ui";
import { ArrowLeft as ArrowLeftIcon } from "@iconoir/vue";
import { apiErrMsg } from "@/api/client";
import {
  acceptAiTasks, askAi, cancelRun, createReview, createTask, exportRun, getPlan, getRun, listAi, listEvidence,
  listFindings, listGaps, listReviews, listRevisions, listRuns, listTasks, patchPlan, patchTask, requestAi,
  startRun, transitionPlan,
  type AIItem, type ChangePlan, type ChangeTask, type ImpactAIArtifact, type ImpactEvidence, type ImpactFinding,
  type ImpactGap, type ImpactReview, type ImpactRun,
} from "@/api/changeImpact";
import { useChangeImpact } from "@/composables/useChangeImpact";
import {
  AiIcon, ArchiveIcon, CancelIcon, ChangeImpactIcon, DownloadIcon, EditIcon, OkIcon, PlayIcon, PlusIcon, RefreshIcon,
  ReviewIcon, SaveIcon, SendIcon,
} from "@/icons";
import ImpactRelationGraph from "@/components/ImpactRelationGraph.vue";
import { iconFor } from "@/utils/impactGraphIcons";
import {
  MIME, pdfPayload, reportDocx, reportOdt, saveBytes, workbookOds, workbookXlsx,
  type Report, type ReportSection, type ReportTone, type Sheet,
} from "@/utils/reportExport";
import { renderReportPdf } from "@/api/reports";
import { saveBlob } from "@/utils/saveFile";
import { useExportBusy } from "@/composables/useExportBusy";
import { fmtDateTime } from "@/utils/datetime";
import { autoSort } from "@/composables/useTableSort";
import { cmpNatural } from "@/utils/sort";
import {
  decisionType, dispositionType, gapAffected, gapText, lifecycleType, reasonText, severityType, taskTitle,
} from "@/utils/changeImpact";

const route = useRoute();
const router = useRouter();
const { t, te, locale } = useI18n();
const msg = useMessage();
const { settings, load: loadSettings } = useChangeImpact();

const plan = ref<ChangePlan | null>(null);
const run = ref<ImpactRun | null>(null);
const runs = ref<ImpactRun[]>([]);
const findings = ref<ImpactFinding[]>([]);
const evidence = ref<ImpactEvidence[]>([]);
const gaps = ref<ImpactGap[]>([]);
const tasks = ref<ChangeTask[]>([]);
const reviews = ref<ImpactReview[]>([]);
const revisions = ref<{ revision: number; payload: any; created_at: string }[]>([]);
const artifacts = ref<ImpactAIArtifact[]>([]);
const picked = reactive<Record<string, number[]>>({});
const loading = ref(true);
const error = ref("");
const busy = ref("");
const openRows = ref<string[]>([]);
function onExpandRows(keys: Array<string | number> | undefined) { openRows.value = (keys ?? []).map(String); }
const tab = ref("findings");
const fDisp = ref<string | null>(null);
const fCat = ref<string | null>(null);
const question = ref("");
let pollTimer: ReturnType<typeof setTimeout> | null = null;
let pollDelay = 2000;

const planId = computed(() => String(route.params.id));
const ACTIVE = ["queued", "snapshotting", "extracting", "analyzing", "persisting"];
const isActive = computed(() => !!run.value && ACTIVE.includes(run.value.job_status));
const isDone = computed(() => !!run.value && ["completed", "partial"].includes(run.value.job_status));
const expired = computed(() => !!run.value?.expires_at && new Date(run.value.expires_at).getTime() < Date.now());
// 已刪除（封存）的計畫只能看：所有操作都收起來（2026-10-25 實測：送審中刪掉的計畫還看得到「覆核」）
const editable = computed(() => !!plan.value && !plan.value.archived_at && !["closed", "cancelled"].includes(plan.value.lifecycle));
const canRun = computed(() => !!plan.value?.can_edit && editable.value && !isActive.value
                              && ["draft", "in_review", "approved"].includes(plan.value.lifecycle));
const phases = ["precheck", "change", "verify", "rollback"] as const;

/** 依目前狀態可以做的動作（後端仍會再檢查一次） */
const actions = computed<string[]>(() => {
  const p = plan.value;
  if (!p) return [];
  const out: string[] = [];
  if (p.archived_at) return out;
  if (p.can_edit && p.lifecycle === "draft" && isDone.value && !expired.value) out.push("submit");
  if (p.lifecycle === "in_review" && p.can_review) out.push("review");
  if (p.can_edit && p.lifecycle === "approved") out.push("start");
  if (p.can_edit && p.lifecycle === "in_progress") out.push("verify");
  if (p.can_edit && p.lifecycle === "verified") out.push("close");
  if (p.can_edit && editable.value) out.push("cancel");
  return out;
});

const canReopen = computed(() => !!plan.value?.can_edit && plan.value.lifecycle === "cancelled"
                                 && !plan.value.archived_at);
const submitConfirmOpen = ref(false);
const submitLead = computed(() => {
  const p = plan.value;
  if (!p) return "";
  const mode = p.review_mode ?? (p.reviewers_designated ? "designated" : "editors");
  if (mode === "editors") return t("change_impact.submit_confirm_admins");
  if (mode === "admin") return t("change_impact.submit_confirm_admin_only");
  if (!p.reviewers?.length) return "";
  if (mode === "stages") return t("change_impact.submit_confirm_stages", { name: p.review_steps?.[0]?.name ?? "1" });
  if (mode === "parallel") return t("change_impact.submit_confirm_parallel", { n: p.review_steps?.length ?? 0 });
  return t("change_impact.submit_confirm_list");
});

async function reopenAndRun() {
  if (!plan.value) return;
  busy.value = "run";
  try {
    plan.value = { ...plan.value, ...(await transitionPlan(plan.value.id, "reopen")) };
    run.value = await startRun(plan.value.id);
    pollDelay = 2000;
    await loadAll();
  } catch (e) { msg.error(apiErrMsg(e)); await loadAll(); } finally { busy.value = ""; }
}

const reviewersText = computed(() => {
  const p = plan.value;
  const list = p?.reviewers ?? [];
  if (!list.length) return p?.reviewers_designated ? t("change_impact.reviewers_none") : "—";
  const shown = list.slice(0, 5).map((r) => r.name).join("、");
  const total = p?.reviewers_total ?? list.length;
  const names = total > 5 ? `${shown} ${t("change_impact.reviewers_more", { n: total })}` : shown;
  return p?.reviewers_designated ? names : t("change_impact.reviewers_admins", { names });
});

/** 推進流程的動作（取消另外放在紅框按鈕、要先確認） */
// 最近一次覆核是退回或駁回、而且還沒重新送審：在最上面講明理由
const returnedReview = computed(() => {
  const r = plan.value?.latest_review;
  return r && (r.decision === "request_changes" || r.decision === "reject")
    && (plan.value?.lifecycle === "draft" || plan.value?.lifecycle === "cancelled") ? r : null;
});
const forwardActions = computed(() => actions.value.filter((a) => a !== "cancel"));
// 有阻擋項目、或有我看不到的需覆核項目：核准與接受風險都不行，只能要求修改或退回
const canApprove = computed(() => run.value?.decision_status !== "blocked" && !run.value?.review_items_hidden);
const ACT_ICONS: Record<string, Component> = {
  submit: SendIcon, review: ReviewIcon, start: PlayIcon, verify: OkIcon, close: ArchiveIcon,
};

const dispOptions = computed(() => ["blocker", "review", "informational"]
  .map((v) => ({ label: t(`change_impact.disposition.${v}`), value: v })));
const catOptions = computed(() => [...new Set(findings.value.map((f) => f.category))]
  .map((v) => ({ label: catLabel(v), value: v })));
const shownFindings = computed(() => findings.value.filter((f) =>
  (!fDisp.value || f.disposition === fDisp.value) && (!fCat.value || f.category === fCat.value)));
const taskStateOptions = computed(() => ["pending", "in_progress", "done", "blocked", "skipped"]
  .map((v) => ({ label: t(`change_impact.task_state.${v}`), value: v })));
// 匯出（使用者 2026-10-08）：PDF／DOCX／ODT 是報告，XLSX／ODS 是表格清單，Markdown／JSON 是原始資料
const exportOptions = computed(() => [
  { type: "group", key: "g-report", label: t("change_impact.export_report"), children: [
    { label: "PDF", key: "pdf" }, { label: "Word (.docx)", key: "docx" }, { label: "OpenDocument (.odt)", key: "odt" }] },
  { type: "group", key: "g-table", label: t("change_impact.export_table"), children: [
    { label: "Excel (.xlsx)", key: "xlsx" }, { label: "OpenDocument (.ods)", key: "ods" }] },
  { type: "group", key: "g-data", label: t("change_impact.export_data"), children: [
    { label: "Markdown", key: "md" }, { label: "JSON", key: "json" }] },
]);
const dispActionOptions = computed(() => ["will_update", "not_needed", "owner_confirm", "accepted"]
  .map((v) => ({ label: t(`change_impact.disp_action.${v}`), value: v })));

function catLabel(c: string): string {
  return te(`change_impact.category.${c}`) ? t(`change_impact.category.${c}`) : c;
}
function subjectTypeLabel(type: string, category: string): string {
  if (te(`change_impact.stype.${type}`)) return t(`change_impact.stype.${type}`);
  return catLabel(category);
}
function errText(code: string): string {
  return te(`errors.${code}`) ? t(`errors.${code}`) : code;
}
function shortRef(id: string): string {
  const i = findings.value.findIndex((f) => f.id === id);
  return i >= 0 ? String(i + 1) : id.slice(0, 6);
}
function tasksOf(ph: string): ChangeTask[] {
  return tasks.value.filter((x) => x.phase === ph).sort((a, b) => a.position - b.position);
}
// AI 執行中做到哪一步（後端 stage）＋經過秒數；排隊中是 pending
const nowTick = ref(Date.now());
let tickTimer: number | undefined;
watch(() => artifacts.value.some((a) => a.status === "pending" || a.status === "running"), (busyAi) => {
  window.clearInterval(tickTimer);
  if (busyAi) tickTimer = window.setInterval(() => { nowTick.value = Date.now(); }, 1000);
}, { immediate: true });
onBeforeUnmount(() => window.clearInterval(tickTimer));
function aiStageText(a: ImpactAIArtifact): string {
  const stage = a.status === "pending" ? "queued" : (a.stage ?? "collecting");
  const secs = Math.max(0, Math.round((nowTick.value - Date.parse(a.created_at)) / 1000));
  const what = te(`change_impact.ai_stage.${stage}`) ? t(`change_impact.ai_stage.${stage}`) : stage;
  return t("change_impact.ai_stage_elapsed", { what, n: secs });
}
const CITES_SHOWN = 6;
const citesOpen = reactive<Record<string, boolean>>({});
function shownCites(aid: string, i: number, ids: string[]): string[] {
  return citesOpen[`${aid}:${i}`] ? ids : ids.slice(0, CITES_SHOWN);
}
function toggleCites(aid: string, i: number) { citesOpen[`${aid}:${i}`] = !citesOpen[`${aid}:${i}`]; }
const findingById = computed(() => Object.fromEntries(findings.value.map((f) => [f.id, f])));
function citeLabel(id: string): string { return findingById.value[id]?.subject_label ?? ""; }
function citeReason(id: string): string {
  const f = findingById.value[id];
  return f ? reasonText(t, te, f.reason_code, f.params) : "";
}

function aiText(it: AIItem): string {
  if (it.text) return it.text;
  if (it.code) return t(`change_impact.ai_tpl.${it.code}`, it.params ?? {});
  return "";
}

const evById = computed(() => Object.fromEntries(evidence.value.map((e) => [e.id, e])));
// 嚴重度依等級排（不是依字母）；其他翻譯過的欄位依畫面上的文字排
const SEV_RANK: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3, info: 4 };
const byText = <T,>(f: (r: T) => string) => (a: T, b: T) => cmpNatural(f(a), f(b));
const findingCols = computed<DataTableColumns<ImpactFinding>>(() => autoSort<ImpactFinding>([
  // 展開的細節：左側色條＋底色、左右兩欄（項目／內容），一眼看得出是上面那一列的（使用者 2026-10-08）
  { type: "expand", renderExpand: (r: ImpactFinding) => h("div", { class: "cip-expand", "data-testid": "cip-expand" }, [
      ...r.evidence_ids.flatMap((id, i) => {
        const e = evById.value[id];
        return e ? [
          h("span", { class: "cip-expand__k" }, i === 0 ? t("change_impact.expand_evidence") : ""),
          h("span", { class: "cip-expand__v" }, [
            h("span", { class: "cip-mono" }, e.label),
            h("span", { class: "cip-muted" }, ` · ${catLabel(e.source_type)} · ${t("change_impact.observed")} `
              + `${e.observed_at ? fmtDateTime(e.observed_at) : "—"} · ${t("change_impact.collected")} `
              + `${e.collected_at ? fmtDateTime(e.collected_at) : "—"}`),
          ]),
        ] : [];
      }),
      ...(r.relationship_path.length ? [
        h("span", { class: "cip-expand__k" }, t("change_impact.path")),
        h("span", { class: "cip-expand__v" }, r.relationship_path.map((p) => p.label).join(" → ")),
      ] : []),
      h("span", { class: "cip-expand__k" }, t("change_impact.expand_rule")),
      h("span", { class: "cip-expand__v cip-muted" }, `${r.rule_id} v${r.rule_version}`),
    ]) },
  { title: t("change_impact.col_disposition"), key: "disposition", width: 96,
    sorter: byText((r: ImpactFinding) => t(`change_impact.disposition.${r.disposition}`)),
    render: (r: ImpactFinding) => h(NTag, { size: "small", type: dispositionType(r.disposition) },
                     { default: () => t(`change_impact.disposition.${r.disposition}`) }) },
  { title: t("change_impact.col_severity"), key: "severity", width: 80,
    sorter: (a: ImpactFinding, b: ImpactFinding) => (SEV_RANK[a.severity] ?? 9) - (SEV_RANK[b.severity] ?? 9),
    render: (r: ImpactFinding) => h(NTag, { size: "small", bordered: false, type: severityType(r.severity) },
                     { default: () => t(`change_impact.severity.${r.severity}`) }) },
  { title: t("change_impact.col_category"), key: "category", width: 96, render: (r: ImpactFinding) => catLabel(r.category),
    sorter: byText((r: ImpactFinding) => catLabel(r.category)) },
  // 物件與原因：第一行是「是什麼」（類型圖示＋類型＋名稱），第二行是「為什麼列出來」（使用者 2026-10-08：要一眼看懂）
  { title: t("change_impact.col_subject"), key: "subject_label", minWidth: 340,
    render: (r: ImpactFinding) => h("div", { id: `cip-f-${r.id}`, class: "cip-subj" }, [
      h("div", { class: "cip-subj__head" }, [
        h("span", { class: "cip-subj__type" }, [h(NIcon, { size: 13 }, () => h(iconFor(r.subject_type))),
                                                h("span", null, subjectTypeLabel(r.subject_type, r.category))]),
        h("span", { class: "cip-strong cip-wrap" }, r.subject_label),
      ]),
      h("div", { class: "cip-subj__why" }, [
        h("span", { class: "cip-subj__k" }, t("change_impact.why")),
        h("span", { class: "cip-wrap" }, reasonText(t, te, r.reason_code, r.params)),
      ]),
    ]) },
  { title: t("change_impact.col_impact"), key: "impact", width: 110, render: (r: ImpactFinding) => t(`change_impact.impact.${r.impact}`),
    sorter: byText((r: ImpactFinding) => t(`change_impact.impact.${r.impact}`)) },
  { title: t("change_impact.col_strength"), key: "evidence_strength", width: 100,
    render: (r: ImpactFinding) => t(`change_impact.strength.${r.evidence_strength}`),
    sorter: byText((r: ImpactFinding) => t(`change_impact.strength.${r.evidence_strength}`)) },
]));
// 欄寬：時間與時效固定且不換行，物件欄吃剩下的寬度（使用者 10-21：物件欄太空、右邊的時間被擠成兩行）
const nowrap = (v: string) => h("span", { style: "white-space: nowrap" }, v);
// 證據來自哪一台整合：同一筆紀錄在兩台 DNS 上會是兩列，只寫「DNS」看起來像重複（2026-10-25 實測）
const sourceNames = computed(() => new Map<string, string>((run.value?.scope_manifest?.sources ?? [])
  .filter((s) => s.id).map((s) => [`${s.kind}:${s.id}`, s.name])));
function evidenceSource(e: ImpactEvidence): string {
  const name = e.integration_ref ? sourceNames.value.get(e.integration_ref) : undefined;
  return name ? `${catLabel(e.source_type)} · ${name}` : catLabel(e.source_type);
}
const freshText = (r: ImpactEvidence) => (te(`change_impact.fresh.${r.freshness}`) ? t(`change_impact.fresh.${r.freshness}`) : r.freshness);
const evidenceCols = computed<DataTableColumns<ImpactEvidence>>(() => autoSort<ImpactEvidence>([
  { title: t("change_impact.col_source"), key: "source_type", width: 170, render: (r: ImpactEvidence) => nowrap(evidenceSource(r)),
    sorter: byText(evidenceSource) },
  { title: t("change_impact.col_object"), key: "label", minWidth: 220,
    render: (r: ImpactEvidence) => h("span", { class: "cip-mono cip-wrap" }, r.label) },
  { title: t("change_impact.observed"), key: "observed_at", width: 180,
    render: (r: ImpactEvidence) => nowrap(r.observed_at ? fmtDateTime(r.observed_at) : "—") },
  { title: t("change_impact.collected"), key: "collected_at", width: 180,
    render: (r: ImpactEvidence) => nowrap(r.collected_at ? fmtDateTime(r.collected_at) : "—") },
  { title: t("change_impact.col_freshness"), key: "freshness", width: 120,
    render: freshText, sorter: byText(freshText) },
]));

/** 來源依種類、名稱排；新鮮度分三級給顏色（最新／即時＝正常，過期與失敗＝要注意，其餘＝不確定） */
const sortedSources = computed(() => [...(run.value?.scope_manifest?.sources ?? [])]
  .sort((a, b) => kindLabel(a.kind).localeCompare(kindLabel(b.kind)) || a.name.localeCompare(b.name)));
function kindLabel(kind: string): string {
  const k = `change_impact.param.kind.${kind}`;
  return te(k) ? t(k) : kind;
}
function freshTone(f: string): "ok" | "bad" | "unknown" {
  if (f === "fresh" || f === "current") return "ok";
  if (f === "stale" || f === "failed" || f === "partial" || f === "never_synced") return "bad";
  return "unknown";
}

async function loadRunData(runId: string) {
  const [r, fs, ev, gp, ai] = await Promise.all([getRun(runId), listFindings(runId), listEvidence(runId),
                                                 listGaps(runId), listAi(runId)]);
  run.value = r;
  findings.value = fs.items;
  evidence.value = ev;
  gaps.value = gp;
  artifacts.value = ai;
}

async function loadAll() {
  error.value = "";
  try {
    const p = await getPlan(planId.value);
    plan.value = p;
    const [rs, tk, rv, revs] = await Promise.all([listRuns(p.id), listTasks(p.id), listReviews(p.id), listRevisions(p.id)]);
    runs.value = rs; tasks.value = tk; reviews.value = rv; revisions.value = revs;
    const latest = rs[0];
    if (latest) {
      // 完成的 run 等資料都載完才換上去：不然摘要先出現、分頁的數字還是 0
      if (["completed", "partial"].includes(latest.job_status)) await loadRunData(latest.id);
      else run.value = latest;
      schedulePoll();
    } else {
      run.value = null;
    }
  } catch (e) {
    error.value = apiErrMsg(e);
  } finally {
    loading.value = false;
  }
}

function schedulePoll() {
  if (pollTimer) clearTimeout(pollTimer);
  const pendingAi = artifacts.value.some((a) => a.status === "pending" || a.status === "running");
  if (!isActive.value && !pendingAi) { pollDelay = 2000; return; }
  pollTimer = setTimeout(async () => {
    if (!run.value) return;
    try {
      const r = await getRun(run.value.id);
      const was = run.value.job_status;
      run.value = r;
      if (ACTIVE.includes(was) && !ACTIVE.includes(r.job_status)) await loadAll();
      else if (pendingAi) artifacts.value = await listAi(r.id);
    } catch { /* 下一輪再試 */ }
    pollDelay = Math.min(10000, pollDelay * 1.5);     // 2 秒起、最多 10 秒（規格 §8.4）
    schedulePoll();
  }, pollDelay);
}

async function rerun() {
  if (!plan.value) return;
  busy.value = "run";
  try {
    run.value = await startRun(plan.value.id);
    pollDelay = 2000;
    await loadAll();
  } catch (e) { msg.error(apiErrMsg(e)); } finally { busy.value = ""; }
}
async function cancel() {
  if (!run.value) return;
  try { run.value = await cancelRun(run.value.id); } catch (e) { msg.error(apiErrMsg(e)); }
}
async function pickRun(id: string) {
  await loadRunData(id);
  tab.value = "findings";
}

const reviewOpen = ref(false);
const review = reactive<{ decision: string; rationale: string; disp: Record<string, string> }>(
  { decision: "approve", rationale: "", disp: {} });
const reviewFindings = computed(() => findings.value.filter((f) => f.disposition === "review"));

async function act(a: string) {
  if (!plan.value) return;
  // 送審：第一次只打開確認對話框（列出會通知的審核人），在對話框按確認才真的送出
  if (a === "submit" && !submitConfirmOpen.value) { submitConfirmOpen.value = true; return; }
  if (a === "review") {
    review.decision = !canApprove.value ? "request_changes"
      : run.value?.completeness === "complete" ? "approve" : "accept_risk";
    reviewOpen.value = true; return;
  }
  busy.value = a;
  try {
    plan.value = { ...plan.value, ...(await transitionPlan(plan.value.id, a)) };
    if (a === "submit") submitConfirmOpen.value = false;
    await loadAll();
  } catch (e) { msg.error(apiErrMsg(e)); await loadAll(); } finally { busy.value = ""; }
}
async function submitReview() {
  if (!plan.value || !run.value) return;
  busy.value = "review";
  try {
    const dispositions = Object.fromEntries(Object.entries(review.disp).filter(([, v]) => v)
      .map(([k, v]) => [k, { action: v, note: "" }]));
    await createReview(plan.value.id, { decision: review.decision, rationale: review.rationale, dispositions,
                                        run_id: run.value.id });
    reviewOpen.value = false;
    await loadAll();
  } catch (e) { msg.error(apiErrMsg(e)); } finally { busy.value = ""; }
}

const editOpen = ref(false);
const edit = reactive({ title: "", new_ip: "" });
function openEdit() {
  if (!plan.value) return;
  edit.title = plan.value.title;
  edit.new_ip = plan.value.parameters.new_ip ?? "";
  editOpen.value = true;
}
async function saveEdit() {
  if (!plan.value) return;
  busy.value = "edit";
  try {
    const body: Record<string, any> = { title: edit.title };
    if (plan.value.scenario_type === "ip_renumber") body.parameters = { new_ip: edit.new_ip.trim() };
    await patchPlan(plan.value.id, plan.value.revision, body);
    editOpen.value = false;
    await loadAll();
  } catch (e) { msg.error(apiErrMsg(e)); } finally { busy.value = ""; }
}

async function setTaskState(tk: ChangeTask, state: string) {
  let note: string | undefined;
  if (state === "skipped") {
    note = window.prompt(t("change_impact.skip_reason")) ?? "";
    if (!note.trim()) return;
  }
  try {
    const upd = await patchTask(tk.id, tk.version, { state, ...(note ? { completion_note: note } : {}) });
    Object.assign(tk, upd);
  } catch (e) { msg.error(apiErrMsg(e)); tasks.value = await listTasks(planId.value); }
}
const newTask = ref<{ phase: string; title: string } | null>(null);
async function saveTask() {
  if (!plan.value || !newTask.value) return;
  try {
    await createTask(plan.value.id, { phase: newTask.value.phase, title: newTask.value.title.trim() });
    newTask.value = null;
    tasks.value = await listTasks(plan.value.id);
  } catch (e) { msg.error(apiErrMsg(e)); }
}

async function genAi(kind: string) {
  if (!run.value) return;
  busy.value = `ai-${kind}`;
  try { await requestAi(run.value.id, kind); artifacts.value = await listAi(run.value.id); schedulePoll(); }
  catch (e) { msg.error(apiErrMsg(e)); } finally { busy.value = ""; }
}
async function ask() {
  if (!run.value || !question.value.trim()) return;
  busy.value = "ask";
  try { await askAi(run.value.id, question.value.trim()); question.value = ""; artifacts.value = await listAi(run.value.id); schedulePoll(); }
  catch (e) { msg.error(apiErrMsg(e)); } finally { busy.value = ""; }
}
async function acceptDrafts(id: string) {
  if (!plan.value) return;
  try {
    await acceptAiTasks(plan.value.id, id, picked[id] ?? []);
    picked[id] = [];
    tasks.value = await listTasks(plan.value.id);
    tab.value = "tasks";
  } catch (e) { msg.error(apiErrMsg(e)); }
}
function focusFinding(id: string) {
  tab.value = "findings";
  fDisp.value = null; fCat.value = null;
  setTimeout(() => document.getElementById(`cip-f-${id}`)?.scrollIntoView({ behavior: "smooth", block: "center" }), 50);
}

/** 影響清單的一列（報告與表格共用同一份欄位） */
function findingRow(f: ImpactFinding): string[] {
  return [t(`change_impact.disposition.${f.disposition}`), t(`change_impact.severity.${f.severity}`), catLabel(f.category),
          subjectTypeLabel(f.subject_type, f.category), f.subject_label, reasonText(t, te, f.reason_code, f.params),
          t(`change_impact.impact.${f.impact}`), t(`change_impact.strength.${f.evidence_strength}`)];
}
function findingCols4Export(): string[] {
  return [t("change_impact.col_disposition"), t("change_impact.col_severity"), t("change_impact.col_category"),
          t("change_impact.export_col_type"), t("change_impact.export_col_object"), t("change_impact.why"),
          t("change_impact.col_impact"), t("change_impact.col_strength")];
}
/** 報告用的影響清單：8 欄塞不進 A4 直式（右邊會超出去），併成 4 欄、資訊不少 ——
 * 處置＋嚴重度、類型（＋類別）＋名稱、原因、影響＋證據強度；阻擋／需覆核的第一欄上色 */
function pair(a: string, b: string): string {
  return `${a} / ${b}`;   // 中日文也用半形斜線加空白（2026-10-24 全站規則；這裡曾特地組成全形）
}
function findingReportRow(f: ImpactFinding): string[] {
  const type = subjectTypeLabel(f.subject_type, f.category);
  const cat = catLabel(f.category);
  return [`${t(`change_impact.disposition.${f.disposition}`)}\n${t(`change_impact.severity.${f.severity}`)}`,
          `${type}${cat && !type.includes(cat) ? ` · ${cat}` : ""}\n${f.subject_label}`,
          reasonText(t, te, f.reason_code, f.params),
          `${t(`change_impact.impact.${f.impact}`)}\n${t(`change_impact.strength.${f.evidence_strength}`)}`];
}
const DISP_TONE: Record<string, ReportTone> = { blocker: "danger", review: "warning" };
const PHASES = ["precheck", "change", "verify", "rollback"];
function exportBase(): string {
  return `change-impact-${(plan.value?.title ?? "plan").replace(/[^\w.\u3400-\u9fff-]+/g, "_").slice(0, 60)}`;
}
function buildReport(): Report {
  const p = plan.value!, r = run.value!;
  const meta: [string, string][] = [
    [t("change_impact.export_target"), p.target_label ?? ""],
    [t("change_impact.export_scenario"), t(`change_impact.scenario.${p.scenario_type}`)],
  ];
  if (p.parameters?.new_ip) meta.push([t("change_impact.f_new_ip"), String(p.parameters.new_ip)]);
  meta.push([t("change_impact.export_revision"), t("change_impact.revision", { n: p.revision })],
            [t("change_impact.export_lifecycle"), t(`change_impact.lifecycle.${p.lifecycle}`)],
            [t("change_impact.col_decision"), r.decision_status ? t(`change_impact.decision.${r.decision_status}`) : "—"],
            [t("change_impact.col_completeness"), r.completeness ? t(`change_impact.completeness.${r.completeness}`) : "—"],
            ...(["blockers", "review", "change_required", "gaps"] as const).map((k) =>
              [t(`change_impact.count.${k}`), String(r.counts?.[k] ?? 0)] as [string, string]),
            [t("change_impact.analyzed_at"), r.completed_at ? fmtDateTime(r.completed_at) : "—"],
            [t("change_impact.valid_until"), r.expires_at ? fmtDateTime(r.expires_at) : "—"]);
  const ai = artifacts.value.find((a) => a.artifact_type === "summary" && !a.hidden_scope_changed
                                       && (a.status === "completed" || a.status === "fallback") && a.output);
  const sections: ReportSection[] = [];
  if (ai?.output) {
    sections.push({ heading: t("change_impact.export_summary"),
                    bullets: (ai.output.summary ?? []).map((it) => aiText(it)).filter(Boolean),
                    caption: t("change_impact.ai_note") });
  }
  sections.push({ heading: t("change_impact.tab_findings", { n: findings.value.length }),
                  table: { cols: [pair(t("change_impact.col_disposition"), t("change_impact.col_severity")),
                                  t("change_impact.export_col_object"), t("change_impact.why"),
                                  pair(t("change_impact.col_impact"), t("change_impact.col_strength"))],
                           rows: findings.value.map(findingReportRow), widths: [15, 28, 43, 14],
                           tones: findings.value.map((f) => DISP_TONE[f.disposition] ?? null) } });
  sections.push({ heading: t("change_impact.gaps_title"),
                  bullets: gaps.value.map((g) => `${catLabel(g.category)}：${gapText(t, te, g.reason_code, g.params)}`) });
  for (const ph of PHASES) {
    const list = tasksOf(ph);
    if (!list.length) continue;
    sections.push({ heading: `${t("change_impact.export_tasks")}：${t(`change_impact.phase.${ph}`)}`,
                    table: { cols: [t("change_impact.export_task_state"), t("change_impact.export_task")], widths: [14, 86],
                             rows: list.map((x) => [t(`change_impact.task_state.${x.state}`), taskTitle(t, te, x)]) } });
  }
  if (reviews.value.length) {
    sections.push({ heading: t("change_impact.reviews_title"), table: {
      cols: [t("change_impact.export_time"), t("change_impact.export_result"), t("change_impact.export_reviewer"),
             t("change_impact.export_revision"), t("change_impact.export_rationale")],
      widths: [20, 13, 16, 9, 42],
      rows: reviews.value.map((rv) => [fmtDateTime(rv.created_at), t(`change_impact.review_decision.${rv.decision}`),
                                       rv.reviewer_name || "—", t("change_impact.revision", { n: rv.revision }),
                                       rv.rationale ?? ""]) } });
  }
  return { title: p.title, meta, sections, brand: "jt-ipam",
           subtitle: [t("change_impact.title"), p.target_label, t("change_impact.revision", { n: p.revision })]
             .filter(Boolean).join(" · "),
           generatedAt: fmtDateTime(new Date().toISOString()),
           footerText: `jt-ipam · ${t("change_impact.title")}`,
           note: t("change_impact.dry_run_notice") };
}
function buildSheets(): Sheet[] {
  return [
    { name: t("change_impact.export_sheet_findings"), cols: findingCols4Export(), rows: findings.value.map(findingRow) },
    { name: t("change_impact.gaps_title"), cols: [t("change_impact.col_category"), t("change_impact.export_gap")],
      rows: gaps.value.map((g) => [catLabel(g.category), gapText(t, te, g.reason_code, g.params)]) },
    { name: t("change_impact.export_tasks"), cols: [t("change_impact.export_phase"), t("change_impact.export_task_state"),
                                                    t("change_impact.export_task")],
      rows: PHASES.flatMap((ph) => tasksOf(ph).map((x) => [t(`change_impact.phase.${ph}`),
                                                           t(`change_impact.task_state.${x.state}`), taskTitle(t, te, x)])) },
    { name: t("change_impact.evidence_title"), cols: [t("change_impact.col_source"), t("change_impact.col_object"),
                                                      t("change_impact.observed"), t("change_impact.collected")],
      rows: evidence.value.map((e) => [evidenceSource(e), e.label, e.observed_at ? fmtDateTime(e.observed_at) : "",
                                       e.collected_at ? fmtDateTime(e.collected_at) : ""]) },
  ];
}

// 選了格式到檔案存好之前：按鈕反灰＋轉圈（使用者 2026-10-08）
const { exporting, run: runExport } = useExportBusy();

async function doExport(fmt: string) {
  if (!run.value || !plan.value) return;
  try {
    if (fmt === "pdf") {
      // 後端排版成真正的 PDF 檔（內嵌中文字型），不再開瀏覽器列印視窗
      const blob = await renderReportPdf(pdfPayload(buildReport(), String(locale.value), exportBase()));
      saveBytes(`${exportBase()}.pdf`, new Uint8Array(await blob.arrayBuffer()), "application/pdf",
                { source: "change-impact-report", audited: true });   // 伺服器排版時已記稽核
      return;
    }
    if (fmt === "docx" || fmt === "odt") {
      saveBytes(`${exportBase()}.${fmt}`, fmt === "docx" ? reportDocx(buildReport()) : reportOdt(buildReport()), MIME[fmt],
                { source: "change-impact-report" });
      return;
    }
    if (fmt === "xlsx" || fmt === "ods") {
      saveBytes(`${exportBase()}.${fmt}`, fmt === "xlsx" ? workbookXlsx(buildSheets()) : workbookOds(buildSheets()), MIME[fmt],
                { source: "change-impact-report" });
      return;
    }
    const blob = await exportRun(run.value.id, fmt as "md" | "json");
    // 伺服器產生這份檔案時已記稽核（impact_export）
    saveBlob(`change-impact-${plan.value.title.replace(/[^\w.-]+/g, "_").slice(0, 40)}.${fmt}`, blob, blob.type,
             { source: "change-impact", audited: true });
  } catch (e) { msg.error(apiErrMsg(e)); }
}

watch(planId, () => { loading.value = true; void loadAll(); });
onMounted(async () => { await loadSettings(); await loadAll(); });
onBeforeUnmount(() => { if (pollTimer) clearTimeout(pollTimer); });
</script>

<style scoped>
.cip-page { display: flex; flex-direction: column; gap: 12px; }
.cip-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
.cip-head__title { display: flex; align-items: center; gap: 8px; font-size: 17px; font-weight: 600; flex-wrap: wrap; }
.cip-meta { display: flex; gap: 14px; flex-wrap: wrap; margin-top: 8px; font-size: 13px; opacity: .85; }
.cip-dryrun { margin-top: 8px; font-size: 12.5px; opacity: .7; }
.cip-running { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.cip-err { color: #d03050; font-size: 13px; }
/* 統計格：每格有框有底色（照異常偵測頁的統計卡），等寬排列、窄了自動換行；阻擋大於 0 整格轉紅（使用者 2026-10-07） */
.cip-summary { display: flex; flex-wrap: wrap; gap: 10px; }
.cip-stat { box-sizing: border-box; flex: 1 1 110px; display: flex; flex-direction: column; gap: 4px; min-width: 0; padding: 10px 14px;
  border-radius: 10px; border: 1px solid var(--n-border-color, rgba(128, 128, 128, 0.28));
  background: rgba(127, 127, 127, 0.04); }
.cip-stat--time { flex: 2 1 200px; }
.cip-stat > .n-tag { align-self: flex-start; }
.cip-stat--bad { border-color: rgba(208, 48, 80, .5); background: rgba(208, 48, 80, .07); }
.cip-stat--bad .cip-stat__v { color: #d03050; }
.cip-stat__k { font-size: 12px; opacity: .6; }
.cip-stat__v { font-size: 22px; font-weight: 600; font-variant-numeric: tabular-nums; line-height: 1.25; }
.cip-warn { color: #d08a00; }
.cip-scope-note { margin-top: 10px; font-size: 12px; opacity: .6; }
.cip-filters { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; }
.cip-empty { padding: 16px 4px; font-size: 13.5px; opacity: .8; line-height: 1.6; }
.cip-section-k { font-size: 12.5px; font-weight: 600; opacity: .7; margin: 6px 0; }
.cip-submit-lead { margin: 0 0 10px; line-height: 1.6; }
.cip-steps { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-top: 8px; font-size: 13px; }
.cip-gt { width: 100%; border-collapse: collapse; font-size: 13px; }
.cip-gt th { text-align: left; font-weight: 600; font-size: 12.5px; padding: 7px 10px; background: rgba(128, 128, 128, .1);
  border-bottom: 1px solid rgba(128, 128, 128, .3); }
.cip-gt td { padding: 7px 10px; border-bottom: 1px solid rgba(128, 128, 128, .18); vertical-align: top; line-height: 1.5; }
.cip-gt tbody tr:nth-child(even) td { background: rgba(128, 128, 128, .04); }
.cip-sources { display: flex; flex-wrap: wrap; gap: 8px; font-size: 13px; }
.cip-source { display: inline-flex; align-items: center; gap: 7px; padding: 4px 10px; border-radius: 6px;
  border: 1px solid rgba(128, 128, 128, .22); background: rgba(128, 128, 128, .04); line-height: 1.4; }
.cip-source__dot { width: 7px; height: 7px; border-radius: 50%; flex: none; }
.cip-source__dot.fr-ok { background: #18a058; }
.cip-source__dot.fr-bad { background: #d03050; }
.cip-source__dot.fr-unknown { background: rgba(128, 128, 128, .55); }
.cip-source__name { font-weight: 600; }
.cip-source__kind { font-size: 12px; opacity: .6; }
.cip-source__fr { font-size: 12px; font-weight: 600; }
.cip-source__fr.fr-bad { color: #d03050; }
.cip-source__fr.fr-unknown { opacity: .65; }
.cip-manual-note { font-size: 12.5px; opacity: .7; margin-bottom: 8px; }
/* 階段分區：淡色標題列＋標題色就夠分辨，不加左側色條（使用者 10-21：看起來很 low） */
.cip-phase { --ph: #2080f0; margin-bottom: 12px; border: 1px solid rgba(128, 128, 128, .2);
  border-radius: 8px; padding: 0 12px 4px; }
.cip-phase--precheck { --ph: #2080f0; }
.cip-phase--change { --ph: #f0a020; }
.cip-phase--verify { --ph: #18a058; }
.cip-phase--rollback { --ph: #d03050; }
.cip-phase__head { display: flex; align-items: baseline; gap: 8px; padding: 9px 0 7px; margin: 0 -12px 2px -12px;
  padding-left: 12px; background: color-mix(in srgb, var(--ph) 8%, transparent); border-radius: 8px 8px 0 0; }
.cip-phase__name { font-weight: 700; font-size: 14px; color: var(--ph); }
.cip-phase__count { font-size: 11px; font-weight: 700; padding: 0 7px; border-radius: 9px; color: #fff; background: var(--ph); }
.cip-phase__desc { font-size: 12px; opacity: .65; }
.cip-task { display: flex; align-items: center; gap: 10px; padding: 6px 0; border-bottom: 1px solid rgba(128, 128, 128, .12); }
.cip-task:last-child { border-bottom: 0; }
.cip-task__body { flex: 1; min-width: 0; font-size: 13.5px; }
.cip-done { text-decoration: line-through; opacity: .6; }
.cip-ask { display: flex; gap: 8px; margin-bottom: 8px; }
.cip-ai-note { font-size: 12px; opacity: .6; margin-bottom: 10px; }
.cip-ai { border-top: 1px solid rgba(128, 128, 128, .2); padding: 10px 0; }
.cip-ai__head { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; font-size: 13px; }
.cip-ai__list { margin: 6px 0; padding-left: 18px; font-size: 13.5px; line-height: 1.7; }
.cip-ai-task { display: flex; gap: 8px; align-items: baseline; font-size: 13px; }
.cip-cites { display: flex; flex-wrap: wrap; gap: 4px 6px; margin: 2px 0 4px; }
.cip-cite { font-size: 12px; cursor: pointer; color: #2080f0; padding: 1px 6px; border-radius: 4px;
  background: rgba(32, 128, 240, .08); max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cip-cite--more { background: transparent; }
.cip-ai__stage { font-size: 12.5px; color: #2080f0; }
.cip-ai__why { font-size: 12.5px; margin-top: 2px; }
.cip-hist { display: grid; gap: 6px 20px; align-items: center; font-size: 13px; justify-content: start; }
.cip-hist--runs { grid-template-columns: repeat(5, max-content); }
.cip-hist--reviews { grid-template-columns: max-content max-content max-content max-content minmax(0, 1fr); }
.cip-hist--revisions { grid-template-columns: repeat(3, max-content); }
.cip-hist__row { display: contents; }
.cip-hist a { cursor: pointer; color: #2080f0; }
.cip-disp { display: flex; gap: 10px; align-items: center; justify-content: space-between; padding: 4px 0; }
.cip-disp__label { font-size: 13px; min-width: 0; flex: 1; }
@media (max-width: 720px) { .cip-stat { flex-basis: calc(50% - 5px); } .cip-stat--time { flex-basis: 100%; } }
</style>

<style>
.cip-mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12.5px; }
.cip-wrap { white-space: normal; word-break: break-word; }
/* 展開的細節：縮排、左側色條、淡藍底，跟上面那一列連在一起 */
.cip-expand { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 5px 14px; margin: 0 0 4px 36px;
  padding: 8px 14px; font-size: 13px; border-left: 3px solid #2080f0; background: rgba(32, 128, 240, .06);
  border-radius: 0 8px 8px 0; }
.cip-expand__k { font-size: 12px; font-weight: 600; opacity: .65; white-space: nowrap; }
.cip-expand__v { min-width: 0; word-break: break-word; }
/* 展開中的那一列也帶同一個底色，看得出兩段是同一筆 */
.cip-row-open > td { background: rgba(32, 128, 240, .06) !important; }
.cip-subj { display: flex; flex-direction: column; gap: 3px; }
.cip-subj__head { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.cip-subj__type { display: inline-flex; align-items: center; gap: 3px; font-size: 11.5px; padding: 0 6px; border-radius: 4px;
  background: rgba(127, 127, 127, .12); white-space: nowrap; align-self: center; }
.cip-subj__why { display: flex; gap: 6px; align-items: baseline; font-size: 13px; opacity: .85; }
.cip-subj__k { font-size: 11.5px; opacity: .6; white-space: nowrap; }
</style>
