import { ref, computed, watch, onMounted } from 'vue'
import { useSetupHandoff } from './useSetupHandoff'
import { apiErrorMessage } from '../../api/client'
import { setupApi } from '../../api/setup'
import { meetsPasswordRequirements } from '../../utils/passwordStrength'
import { generateRandomPassword } from '../../utils/randomPassword'

// Static dropdown options
const DB_TYPE_OPTIONS = [
  { label: 'MariaDB', value: 'mariadb' },
  { label: 'PostgreSQL', value: 'postgres' },
]
const STEP_TITLES = {
  passwords: 'Admin password',
  database: 'Database',
  customize: 'Customize your bench',
  running: 'Setting up your bench',
  done: 'Setup complete',
}
const STEP_SUBTITLES = {
  database: 'Choose and configure your database',
}

export function useSetup() {
  const { awaitingTerminal } = useSetupHandoff()

  // Wizard state
  const currentStep = ref('loading')
  const errorMessage = ref('')
  const isSubmitting = ref(false)
  const benchName = ref('')
  const isLinux = ref(true)
  const isProductionHandoff = ref(false)
  const availableBranches = ref([])
  const adminPasswordConfigured = ref(false)
  const mariadbPasswordConfigured = ref(false)
  const postgresPasswordConfigured = ref(false)
  const mariadbLocalAvailable = ref(false)
  const postgresLocalAvailable = ref(false)

  const terminal = ref(null)
  const setupTaskId = ref('')
  const streamUrl = ref('')
  const streamStatus = ref('Starting…')
  const showStreamDetails = ref(false)

  // User inputs
  const adminPassword = ref('')
  const dbType = ref('mariadb')
  const dbUser = ref('')
  const dbPassword = ref('')
  const appRepo = ref('https://github.com/frappe/frappe')
  const appBranch = ref('develop')

  const localAvailable = computed(() =>
    dbType.value === 'postgres' ? postgresLocalAvailable.value : mariadbLocalAvailable.value,
  )
  const dbMode = ref('new')
  const dbModeOptions = computed(() => [
    localAvailable.value
      ? { label: 'Use existing database', value: 'existing_local' }
      : { label: 'Create new database', value: 'new' },
    { label: 'Connect to external database', value: 'external' },
  ])
  watch(
    localAvailable,
    (available) => {
      if (dbMode.value !== 'external') dbMode.value = available ? 'existing_local' : 'new'
    },
    { immediate: true },
  )
  const dbHost = ref('')
  const dbPort = ref('')

  const isAdminPasswordValid = computed(
    () => adminPasswordConfigured.value || meetsPasswordRequirements(adminPassword.value),
  )
  const dbPasswordConfigured = computed(() =>
    dbType.value === 'postgres'
      ? postgresPasswordConfigured.value
      : mariadbPasswordConfigured.value,
  )

  const rootUserPlaceholder = computed(() => (dbType.value === 'mariadb' ? 'root' : 'postgres'))
  const dbPortPlaceholder = computed(() => (dbType.value === 'mariadb' ? '3306' : '5432'))
  const resolvedDbUser = computed(() => dbUser.value || rootUserPlaceholder.value)

  const branchOptions = computed(() => {
    const selected = appBranch.value
    const isKnown = availableBranches.value.includes(selected)
    const options = availableBranches.value.map((branch) => ({ label: branch, value: branch }))
    return selected && !isKnown ? [{ label: selected, value: selected }, ...options] : options
  })

  // Steps
  const stepSequence = computed(() => ['passwords', 'database', 'customize'])
  const stepNumber = computed(() => stepSequence.value.indexOf(currentStep.value) + 1)
  const isConfiguring = computed(() => stepNumber.value > 0)
  const isInstalling = computed(() => currentStep.value === 'running')
  const isLastConfigStep = computed(() => currentStep.value === stepSequence.value.at(-1))
  const modalWidthClass = computed(() =>
    isInstalling.value && showStreamDetails.value ? 'max-w-2xl' : 'max-w-lg',
  )
  const isDone = computed(() => currentStep.value === 'done')
  const pilotCommand = computed(() => (benchName.value ? `pilot -b ${benchName.value}` : 'pilot'))
  const stepTitle = computed(() => {
    if (isDone.value && isProductionHandoff.value) return 'Finishing setup'
    return STEP_TITLES[currentStep.value] || benchName.value
  })
  const stepSubtitle = computed(() => STEP_SUBTITLES[currentStep.value] || null)

  // Loading
  async function loadConfig() {
    try {
      const config = await setupApi.config()
      benchName.value = config.bench_name || ''
      isLinux.value = config.is_linux !== false
      adminPasswordConfigured.value = config.admin_password_configured === true
      mariadbPasswordConfigured.value = config.mariadb_password_configured === true
      postgresPasswordConfigured.value = config.postgres_password_configured === true
      // Bench arrived with production already chosen (the admin UI's "New Bench"
      // flow) - the wizard's task will bring up production itself, so the 'done'
      // step shouldn't tell the user to run `pilot setup production` by hand.
      // The flattened config renders an unset manager as the literal string
      // "none" (see BenchTomlBuilder._flatten), not an empty value.
      const processManager = config.production_process_manager
      isProductionHandoff.value = Boolean(processManager) && processManager !== 'none'

      mariadbLocalAvailable.value = config.mariadb_local_available === true
      postgresLocalAvailable.value = config.postgres_local_available === true

      if (config.db_type) dbType.value = config.db_type
      if (config.app_repo) appRepo.value = config.app_repo
      if (config.app_branch) appBranch.value = config.app_branch
      if (config.db_type === 'postgres') {
        if (config.postgres_admin_user) dbUser.value = config.postgres_admin_user
        if (config.postgres_existing) {
          dbMode.value = 'external'
          dbHost.value = config.postgres_host || '127.0.0.1'
          dbPort.value = config.postgres_port ? String(config.postgres_port) : '5432'
        }
      } else {
        if (config.mariadb_admin_user) dbUser.value = config.mariadb_admin_user
        if (config.mariadb_existing) {
          dbMode.value = 'external'
          dbHost.value = config.mariadb_host || '127.0.0.1'
          dbPort.value = config.mariadb_port ? String(config.mariadb_port) : '3306'
        }
      }

      if (config.running_setup_task_id) startStream(config.running_setup_task_id)
      else currentStep.value = 'passwords'
    } catch {
      if (currentStep.value === 'loading') currentStep.value = 'passwords'
    }
    loadBranches()
  }

  async function loadBranches() {
    try {
      availableBranches.value = (await setupApi.branches()).branches || []
    } catch {
      availableBranches.value = []
    }
  }

  // Stream
  function startStream(taskId) {
    setupTaskId.value = taskId
    streamStatus.value = 'Starting…'
    streamUrl.value = setupApi.streamUrl(taskId)
    currentStep.value = 'running'
  }

  function updateStreamStatus(line) {
    const match = line.match(/^\[\d+\/\d+\]\s*(.+?)\.*\s*$/)
    if (match) streamStatus.value = match[1]
  }

  function onStreamDone(success) {
    if (!success) {
      failInstall('Setup failed. Open the details to see what went wrong, then try again.')
      return
    }
    currentStep.value = 'done'
    awaitingTerminal.value = true
    shutdownWizardAndReload()
  }

  function failInstall(message) {
    errorMessage.value = message
    showStreamDetails.value = true
  }

  function toggleStreamDetails() {
    showStreamDetails.value = !showStreamDetails.value
    if (showStreamDetails.value) terminal.value?.scrollToBottom()
  }

  // Validation
  function validatePasswordStep() {
    if (!adminPassword.value && !adminPasswordConfigured.value) return 'Admin password is required'
    if (!adminPassword.value) return null
    if (!meetsPasswordRequirements(adminPassword.value))
      return 'Password does not meet all requirements'
    return null
  }

  async function validateDatabaseStep() {
    if (dbMode.value !== 'external') return null
    const databaseName = dbType.value === 'postgres' ? 'PostgreSQL' : 'MariaDB'
    if (!dbHost.value) return 'Host is required for an external database'
    if (!dbPassword.value && !dbPasswordConfigured.value)
      return `${databaseName} password is required`
    if (!dbPassword.value) return null
    isSubmitting.value = true
    try {
      const result = await setupApi.validateDatabase({
        engine: dbType.value,
        password: dbPassword.value,
        admin_user: resolvedDbUser.value,
        existing: true,
        host: dbHost.value,
        port: Number(dbPort.value) || Number(dbPortPlaceholder.value),
      })
      if (result.error) {
        return apiErrorMessage(result, `Could not validate the ${databaseName} configuration.`)
      }
      if (result.state === 'invalid') return `Incorrect ${databaseName} credentials.`
      if (result.state !== 'valid') {
        return `Could not validate the ${databaseName} configuration.`
      }
    } catch (error) {
      return error.message || `Could not validate the ${databaseName} configuration.`
    } finally {
      isSubmitting.value = false
    }
    return null
  }

  // Navigation
  async function goToNextStep() {
    const validators = { passwords: validatePasswordStep, database: validateDatabaseStep }
    const message = await validators[currentStep.value]?.()
    if (message) {
      errorMessage.value = message
      return
    }
    errorMessage.value = ''
    currentStep.value = stepSequence.value[stepSequence.value.indexOf(currentStep.value) + 1]
  }

  function goToPreviousStep() {
    errorMessage.value = ''
    currentStep.value = stepSequence.value[stepSequence.value.indexOf(currentStep.value) - 1]
  }

  function backToConfiguration() {
    errorMessage.value = ''
    showStreamDetails.value = false
    currentStep.value = stepSequence.value.at(-1)
  }

  function buildPayload() {
    const base = {
      db_type: dbType.value,
      app_repo: appRepo.value,
      app_branch: appBranch.value,
      ...(adminPassword.value ? { admin_password: adminPassword.value } : {}),
    }
    if (dbMode.value === 'existing_local') {
      return { ...base, db_mode: 'existing_local' }
    }
    if (dbMode.value === 'new') {
      const passwordField = dbType.value === 'postgres' ? 'postgres_password' : 'mariadb_password'
      return {
        ...base,
        ...(dbPasswordConfigured.value ? {} : { [passwordField]: generateRandomPassword() }),
      }
    }
    const port = Number(dbPort.value) || undefined
    if (dbType.value === 'postgres') {
      return {
        ...base,
        ...(dbPassword.value ? { postgres_password: dbPassword.value } : {}),
        postgres_admin_user: resolvedDbUser.value,
        postgres_existing: true,
        postgres_host: dbHost.value,
        ...(port ? { postgres_port: port } : {}),
      }
    }
    return {
      ...base,
      ...(dbPassword.value ? { mariadb_password: dbPassword.value } : {}),
      mariadb_admin_user: resolvedDbUser.value,
      mariadb_existing: true,
      mariadb_host: dbHost.value,
      ...(port ? { mariadb_port: port } : {}),
    }
  }

  async function saveConfig() {
    const result = await setupApi.save(buildPayload())
    if (result.error) throw new Error(apiErrorMessage(result, 'Failed to save configuration.'))
  }

  async function startSetup() {
    isSubmitting.value = true
    try {
      await saveConfig()
      const result = await setupApi.start()
      if (result.error) throw new Error(apiErrorMessage(result, 'Failed to start setup.'))
      if (!result.task_id) throw new Error('Setup did not return a task to follow.')
      startStream(result.task_id)
    } catch (error) {
      errorMessage.value = error.message
    } finally {
      isSubmitting.value = false
    }
  }

  async function shutdownWizardAndReload() {
    while (setupTaskId.value) {
      try {
        const response = await setupApi.finish(setupTaskId.value)
        if (response.ok) break
      } catch {}
      await new Promise((resolve) => setTimeout(resolve, 3000))
    }
    while (true) {
      await new Promise((resolve) => setTimeout(resolve, 3000))
      try {
        const bootstrap = await setupApi.bootstrap()
        if (bootstrap.mode === 'admin') return (window.location.href = '/sites')
      } catch {}
    }
  }

  onMounted(loadConfig)

  return {
    currentStep,
    errorMessage,
    isSubmitting,
    isLinux,
    isProductionHandoff,
    isDone,
    pilotCommand,
    terminal,
    streamUrl,
    streamStatus,
    showStreamDetails,
    isAdminPasswordValid,
    adminPassword,
    dbType,
    dbUser,
    dbPassword,
    dbMode,
    dbModeOptions,
    dbHost,
    dbPort,
    dbPortPlaceholder,
    appRepo,
    appBranch,
    rootUserPlaceholder,
    dbTypeOptions: DB_TYPE_OPTIONS,
    branchOptions,
    stepSequence,
    stepNumber,
    isConfiguring,
    isInstalling,
    isLastConfigStep,
    modalWidthClass,
    stepTitle,
    stepSubtitle,
    goToNextStep,
    goToPreviousStep,
    startSetup,
    backToConfiguration,
    toggleStreamDetails,
    updateStreamStatus,
    onStreamDone,
    failInstall,
  }
}
