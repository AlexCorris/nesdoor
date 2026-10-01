#ifndef NESDOOR_DOOR_H
#define NESDOOR_DOOR_H
#include <stddef.h>
int door_open(const char *arg);
void door_close(void);
int door_write(const void *buf, size_t len);
int door_printf(const char *fmt, ...);
int door_read(unsigned char *buf, size_t maxlen, int timeout_ms);
const char *door_alias(void);
int door_connected(void);
#endif
