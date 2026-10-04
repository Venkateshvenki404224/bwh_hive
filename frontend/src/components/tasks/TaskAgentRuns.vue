<template>
	<div class="space-y-1.5">
		<div class="flex items-center justify-between gap-2">
			<FormLabel label="Agent runs" />
			<Button
				v-if="!readOnly && activeAgents.length"
				variant="ghost"
				icon-left="lucide-play"
				label="Run agent"
				:loading="queueing"
				@click="dialogOpen = true"
			/>
		</div>

		<div v-if="runs.loading && !runs.data" class="space-y-1.5">
			<Skeleton v-for="n in 2" :key="n" class="h-9 w-full rounded-4" />
		</div>

		<ul
			v-else-if="runs.data?.length"
			class="divide-y divide-outline-gray-1 rounded-4 border border-outline-gray-1"
		>
			<li v-for="run in runs.data" :key="run.name" class="flex items-center gap-2 px-3 py-2">
				<Badge :label="run.status" :theme="themeFor(run.status)" variant="subtle" />
				<div class="min-w-0 flex-1">
					<p class="truncate text-sm text-ink-gray-8">{{ run.agent }}</p>
					<p class="truncate text-xs text-ink-gray-5">
						<span v-if="run.total_tokens">{{ run.total_tokens }} tokens</span>
						<span v-if="run.latency_ms">· {{ latencySeconds(run) }}s</span>
						<span v-if="run.status === 'Failed' && run.error" class="text-ink-red-3">
							· {{ run.error }}
						</span>
					</p>
				</div>
				<span class="shrink-0 text-xs text-ink-gray-5">{{ fromNow(run.creation) }}</span>
			</li>
		</ul>

		<p v-else class="text-sm text-ink-gray-5">
			{{
				activeAgents.length
					? 'No runs yet.'
					: 'No active agents. Create one in Settings → AI Agents.'
			}}
		</p>
	</div>

	<Dialog v-model:open="dialogOpen" title="Run an agent on this task" size="sm">
		<template #default="{ close }">
			<FormControl
				v-model="selectedAgent"
				label="Agent"
				type="select"
				:options="agentOptions"
			/>
			<p class="pt-2 text-sm text-ink-gray-5">
				It runs in the background and posts its result as a comment. An agent without
				<i>May change task status</i> cannot close this task.
			</p>
			<div class="flex justify-end gap-2 pt-6">
				<Button label="Cancel" @click="close" />
				<Button
					variant="solid"
					theme="gray"
					label="Run"
					:loading="queueing"
					:disabled="!selectedAgent"
					@click="queueRun(close)"
				/>
			</div>
		</template>
	</Dialog>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import {
	Badge,
	Button,
	Dialog,
	FormControl,
	FormLabel,
	Skeleton,
	toast,
	useCall,
	useList,
} from 'frappe-ui'
import { fromNow } from '@/lib/dates'

const props = defineProps<{ taskName: string; readOnly?: boolean }>()

interface AgentRun {
	name: string
	agent: string
	status: 'Queued' | 'Running' | 'Done' | 'Failed'
	total_tokens: number
	latency_ms: number
	error: string | null
	creation: string
}

const runs = useList<AgentRun>({
	doctype: 'Hive Agent Run',
	fields: ['name', 'agent', 'status', 'total_tokens', 'latency_ms', 'error', 'creation'],
	filters: { task: props.taskName },
	orderBy: 'creation desc',
	limit: 20,
})

const agents = useList<{ name: string; agent_name: string }>({
	doctype: 'Hive Agent',
	fields: ['name', 'agent_name'],
	filters: { is_active: 1 },
	orderBy: 'agent_name asc',
	limit: 100,
	cacheKey: 'hive-active-agents',
})

const runAgent = useCall<{ run: string }, { task: string; agent: string }>({
	url: '/api/v2/method/bwh_hive.bwh_hive.agent_runner.run_agent_on_task',
	method: 'POST',
	immediate: false,
})

const dialogOpen = ref(false)
const selectedAgent = ref('')
const queueing = ref(false)

const activeAgents = computed(() => agents.data ?? [])

const agentOptions = computed(() => [
	{ label: 'Select an agent…', value: '' },
	...activeAgents.value.map((agent) => ({ label: agent.agent_name, value: agent.name })),
])

function latencySeconds(run: AgentRun) {
	return (run.latency_ms / 1000).toFixed(1)
}

function themeFor(status: AgentRun['status']) {
	if (status === 'Done') return 'green'
	if (status === 'Failed') return 'red'
	if (status === 'Running') return 'blue'
	return 'gray'
}

async function queueRun(close: () => void) {
	if (!selectedAgent.value || queueing.value) return
	queueing.value = true
	await runAgent.submit({ task: props.taskName, agent: selectedAgent.value })
	queueing.value = false

	// useCall.submit() resolves on an API error too, so the error ref decides.
	if (runAgent.error) {
		toast.error(
			runAgent.error instanceof Error ? runAgent.error.message : 'Could not queue the run',
		)
		return
	}

	close()
	selectedAgent.value = ''
	runs.reload()
	toast.success('Queued — the result will appear as a comment')
}
</script>
