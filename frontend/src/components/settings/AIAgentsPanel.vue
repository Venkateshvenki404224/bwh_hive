<template>
	<SettingsHeader
		title="AI Agents"
		description="Connect Amazon Bedrock so agents can run on your tasks."
	>
		<template v-if="connected" #actions>
			<Badge label="Connected" theme="green" variant="subtle" />
			<Dropdown :options="actions" align="end">
				<Button variant="ghost" icon="lucide-ellipsis" aria-label="Bedrock actions" />
			</Dropdown>
		</template>
	</SettingsHeader>

	<SettingsBody>
		<div v-if="status.loading && !status.data" class="space-y-3 pt-6">
			<Skeleton class="h-6 w-48 rounded-full" />
			<Skeleton class="h-4 w-72 rounded-full" />
			<Skeleton class="h-9 w-40 rounded-4" />
		</div>

		<div v-else class="flex flex-col gap-8 pt-6">
			<section class="space-y-3">
				<div class="space-y-1.5 rounded-4 border border-dashed border-outline-gray-2 p-4">
					<p class="text-sm font-medium text-ink-gray-8">Use a Bedrock-only IAM user</p>
					<p class="text-sm text-ink-gray-6">
						These credentials are stored on this site. Scope them to Bedrock alone —
						never a root or broadly-privileged key — so a database leak cannot reach the
						rest of your AWS account.
					</p>
					<p class="text-sm text-ink-gray-5">
						Required actions: {{ requiredActions.join(', ') }}
					</p>
				</div>

				<div class="grid gap-3 sm:grid-cols-2">
					<div class="space-y-1.5">
						<FormControl
							v-model="form.access_key_id"
							label="AWS Access Key ID"
							type="text"
							:placeholder="status.data?.access_key_id ?? 'AKIA…'"
						/>
						<p v-if="status.data?.access_key_id" class="text-sm text-ink-gray-5">
							Saved: {{ status.data.access_key_id }}
						</p>
					</div>
					<FormControl
						v-model="form.region"
						label="AWS Region"
						type="text"
						placeholder="us-east-1"
					/>
				</div>

				<FormControl
					v-model="form.secret_access_key"
					label="AWS Secret Access Key"
					type="password"
					:placeholder="configured ? 'Stored — type to replace' : ''"
					autocomplete="off"
				/>

				<div class="flex items-center gap-2">
					<Button
						variant="solid"
						theme="gray"
						icon-left="lucide-save"
						label="Save credentials"
						:loading="saving"
						:disabled="!canSave"
						@click="save"
					/>
					<Button
						icon-left="lucide-plug"
						label="Test connection"
						:loading="testing"
						:disabled="!configured"
						@click="test"
					/>
				</div>
			</section>

			<section v-if="status.data?.last_error" class="space-y-2">
				<h3 class="text-base font-semibold text-ink-gray-8">Last error</h3>
				<p
					class="rounded-4 border border-outline-red-2 bg-surface-red-1 p-3 font-mono text-sm text-ink-red-3"
				>
					{{ status.data.last_error }}
				</p>
			</section>

			<section v-if="connected" class="space-y-1">
				<h3 class="text-base font-semibold text-ink-gray-8">Connection</h3>
				<p class="text-sm text-ink-gray-6">
					Verified {{ status.data?.verified_at }} in {{ status.data?.region }}.
				</p>
			</section>
		</div>
	</SettingsBody>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import {
	Badge,
	Button,
	Dropdown,
	FormControl,
	SettingsBody,
	SettingsHeader,
	Skeleton,
	toast,
	useCall,
	useDoc,
} from 'frappe-ui'
import type { DropdownOptions } from 'frappe-ui'

interface BedrockStatus {
	configured: boolean
	access_key_id: string | null
	region: string
	connected: boolean
	verified_at: string | null
	last_error: string | null
	required_actions: string[]
}

const status = useCall<BedrockStatus>({
	url: '/api/v2/method/bwh_hive.bwh_hive.bedrock.status',
	method: 'GET',
	cacheKey: 'bedrock-status',
})

const testCall = useCall<{ connected: boolean; error?: string }>({
	url: '/api/v2/method/bwh_hive.bwh_hive.bedrock.test_connection',
	method: 'POST',
	immediate: false,
})

const disconnectCall = useCall<{ disconnected: boolean }>({
	url: '/api/v2/method/bwh_hive.bwh_hive.bedrock.disconnect',
	method: 'POST',
	immediate: false,
})

const settings = useDoc<{ name: string }>({
	doctype: 'Hive Settings',
	name: 'Hive Settings',
})

const form = reactive({ access_key_id: '', secret_access_key: '', region: '' })
const saving = ref(false)
const testing = ref(false)

const configured = computed(() => Boolean(status.data?.configured))
const connected = computed(() => Boolean(status.data?.connected))
const requiredActions = computed(() => status.data?.required_actions ?? [])

// A first-time save needs both halves; once stored, either can be updated alone.
const canSave = computed(() => {
	if (configured.value) {
		return Boolean(
			form.access_key_id.trim() || form.secret_access_key.trim() || form.region.trim(),
		)
	}
	return Boolean(form.access_key_id.trim() && form.secret_access_key.trim())
})

async function save() {
	if (!canSave.value || saving.value) return
	saving.value = true

	const values: Record<string, string> = {}
	if (form.access_key_id.trim()) values.aws_access_key_id = form.access_key_id.trim()
	if (form.secret_access_key.trim()) values.aws_secret_access_key = form.secret_access_key.trim()
	if (form.region.trim()) values.aws_region = form.region.trim()

	try {
		await settings.setValue.submit(values)
		form.secret_access_key = ''
		await status.reload()
		toast.success('Credentials saved')
	} catch (error) {
		toast.error(error instanceof Error ? error.message : 'Could not save the credentials')
	} finally {
		saving.value = false
	}
}

async function test() {
	if (testing.value) return
	testing.value = true
	const result = await testCall.submit()
	testing.value = false
	await status.reload()

	// useCall.submit() resolves on an API error too, so check the error ref.
	if (testCall.error) {
		toast.error(
			testCall.error instanceof Error ? testCall.error.message : 'Could not reach Bedrock',
		)
		return
	}

	if (result?.connected) toast.success('Bedrock connected')
	else toast.error(result?.error || 'Bedrock rejected the credentials')
}

const actions = computed<DropdownOptions>(() => [
	{
		label: 'Test connection',
		icon: 'lucide-plug',
		onClick: () => test(),
	},
	{
		label: 'Disconnect Bedrock',
		icon: 'lucide-unlink',
		theme: 'red',
		onClick: () => disconnect(),
	},
])

async function disconnect() {
	await disconnectCall.submit()
	if (disconnectCall.error) {
		toast.error('Could not disconnect')
		return
	}
	form.access_key_id = ''
	form.secret_access_key = ''
	await status.reload()
	toast.success('Bedrock disconnected')
}
</script>
