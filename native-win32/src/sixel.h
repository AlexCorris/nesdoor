#ifndef NESDOOR_SIXEL_H
#define NESDOOR_SIXEL_H
#include <stdint.h>
#include <stddef.h>
typedef struct { unsigned char *p; size_t n, cap; } nd_buf;
void nd_buf_free(nd_buf *b);
int sixel_encode_rgb(const uint8_t *rgb,int w,int h,nd_buf *out);
int sixel_encode_scaled(const uint8_t *rgb,int w,int h,int ow,int oh,nd_buf *out);
#endif
