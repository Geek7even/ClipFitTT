// SPDX-License-Identifier: GPL-2.0-only
// Fixed ABI for Python; only Apple's public logging macros are used here.
#include <os/log.h>
#include <pthread.h>
#include <string.h>

static const char *categories[] = {
    "app", "probe", "encode", "publish", "update", "install"
};
static os_log_t handles[6];
static pthread_once_t initialized = PTHREAD_ONCE_INIT;

static void initialize_logs(void)
{
    for (int i = 0; i < 6; ++i) {
        handles[i] = os_log_create("io.github.geek7even.clipfittt", categories[i]);
    }
}

// Return the fixed ABI version checked before Python emits any events.
int clipfittt_log_abi_version(void)
{
    return 1;
}

// Caller provides controlled public fields and separate private detail.
// Return nonzero for invalid arguments; logging availability stays optional.
int clipfittt_log_emit(int level, const char *category, const char *event,
                      const char *batch, const char *task, const char *fields,
                      const char *detail)
{
    static const os_log_type_t levels[] = {
        OS_LOG_TYPE_DEFAULT, OS_LOG_TYPE_INFO, OS_LOG_TYPE_DEBUG,
        OS_LOG_TYPE_ERROR, OS_LOG_TYPE_FAULT
    };
    if (level < 0 || level > 4 || !category || !event || !batch || !task || !fields) {
        return 1;
    }
    pthread_once(&initialized, initialize_logs);
    int index = 0;
    for (int i = 0; i < 6; ++i) {
        if (strcmp(category, categories[i]) == 0) {
            index = i;
            break;
        }
    }
    // The format must be a literal: dynamic strings carry explicit privacy.
    os_log_with_type(handles[index], levels[level],
                     "event=%{public}s batch=%{public}s task=%{public}s data=%{public}s detail=%{private}s",
                     event, batch, task, fields, detail ? detail : "");
    return 0;
}
