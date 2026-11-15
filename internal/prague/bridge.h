// C ABI for the pinned UDP-Prague controller; all calls are serialized by Go.
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef struct { uint64_t rate, size; int32_t window, burst, srtt; int64_t alpha; } prague_info;
void* prague_new(uint64_t rate);
void prague_delete(void* p);
void prague_update(void* p, int32_t now, int32_t received, int32_t ce, int32_t lost, int32_t sent, int32_t rtt);
prague_info prague_get(void* p, int32_t now);
#ifdef __cplusplus
}
#endif
