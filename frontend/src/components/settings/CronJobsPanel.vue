<template>
	<SettingsHeader
		title="Cron Jobs"
		description="Scheduled agent runs, mirrored from Hermes. Filter by project to see what's running where."
	/>
	<SettingsBody>
		<div class="flex flex-col gap-4 pt-6">
			<div class="flex items-center justify-between gap-2">
				<Select
					v-model="projectFilter"
					class="w-56"
					:options="projectOptions"
					aria-label="Filter by project"
				/>
				<span class="text-sm text-ink-gray-5">
					{{ filteredJobs.length }} of {{ jobs.data?.length ?? 0 }} jobs
				</span>
			</div>

			<div v-if="jobs.loading && !jobs.data" class="space-y-2">
				<Skeleton v-for="n in 4" :key="n" class="h-14 w-full rounded-5" />
			</div>
			<ul
				v-else-if="filteredJobs.length"
				class="divide-y divide-outline-gray-1 rounded-5 border border-outline-gray-1"
			>
				<li v-for="job in filteredJobs" :key="job.name" class="flex items-center gap-3 px-4 py-3">
					<div class="min-w-0 flex-1">
						<div class="flex items-center gap-2">
							<p class="truncate text-sm font-medium text-ink-gray-8">{{ job.job_name }}</p>
							<Badge
								v-if="job.last_status"
								:label="job.last_status"
								:theme="job.last_status === 'ok' ? 'green' : job.last_status === 'error' ? 'red' : 'gray'"
								variant="subtle"
							/>
						</div>
						<p class="truncate text-xs text-ink-gray-5">
							{{ projectTitle(job.project) }}
							<span v-if="job.schedule"> &middot; {{ job.schedule }}</span>
							<span v-if="job.deliver_to"> &middot; delivers to {{ job.deliver_to }}</span>
						</p>
					</div>
					<Switch
						:model-value="job.enabled === 1"
						:aria-label="`Toggle ${job.job_name}`"
						@update:model-value="(v: boolean) => toggleEnabled(job, v)"
					/>
				</li>
			</ul>
			<EmptyState
				v-else
				icon="lucide-timer"
				title="No cron jobs recorded yet"
				description="Mirrored from Hermes crons — none match this filter."
			/>
		</div>
	</SettingsBody>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import {
	Badge,
	Select,
	SettingsBody,
	SettingsHeader,
	Skeleton,
	Switch,
	toast,
	useDoctype,
	useList,
} from 'frappe-ui'
import EmptyState from '@/components/common/EmptyState.vue'
import type { HiveCronJob } from '@/types'

const jobs = useList<HiveCronJob>({
	doctype: 'Hive Cron Job',
	fields: [
		'name',
		'job_name',
		'job_id',
		'project',
		'schedule',
		'deliver_to',
		'enabled',
		'last_status',
	],
	orderBy: 'project asc, job_name asc',
	limit: 200,
	cacheKey: 'hive-cron-jobs',
})

const projects = useList<{ name: string; title: string }>({
	doctype: 'Hive Project',
	fields: ['name', 'title'],
	limit: 200,
	cacheKey: 'hive-cron-jobs-project-titles',
})

const projectTitle = (projectName: string | null) => {
	if (!projectName) return 'No project'
	return projects.data?.find((p) => p.name === projectName)?.title ?? projectName
}

const projectFilter = ref('')

const projectOptions = computed(() => {
	const seen = new Map<string, string>()
	for (const job of jobs.data ?? []) {
		if (job.project) seen.set(job.project, projectTitle(job.project))
	}
	return [
		{ label: 'All projects', value: '' },
		{ label: 'No project', value: '__none__' },
		...Array.from(seen.entries())
			.sort((a, b) => a[1].localeCompare(b[1]))
			.map(([value, label]) => ({ label, value })),
	]
})

const filteredJobs = computed(() => {
	const all = jobs.data ?? []
	if (!projectFilter.value) return all
	if (projectFilter.value === '__none__') return all.filter((j) => !j.project)
	return all.filter((j) => j.project === projectFilter.value)
})

const cronJobDoctype = useDoctype<HiveCronJob>('Hive Cron Job')

async function toggleEnabled(job: HiveCronJob, value: boolean) {
	try {
		await cronJobDoctype.setValue.submit({ name: job.name, enabled: value ? 1 : 0 })
		jobs.reload()
		toast.success(
			value
				? `${job.job_name} marked enabled (toggle the real schedule in Hermes too)`
				: `${job.job_name} marked disabled (toggle the real schedule in Hermes too)`,
		)
	} catch (error) {
		toast.error(error instanceof Error ? error.message : 'Could not update the job')
	}
}
</script>
