#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <stdint.h>
#include "sixel.h"

static int put(nd_buf *b,const void *p,size_t n){
 if(b->n+n>b->cap){size_t c=b->cap?b->cap:16384;while(c<b->n+n)c*=2;void*q=realloc(b->p,c);if(!q)return 0;b->p=q;b->cap=c;}
 memcpy(b->p+b->n,p,n);b->n+=n;return 1;
}
static int putsx(nd_buf*b,const char*s){return put(b,s,strlen(s));}
void nd_buf_free(nd_buf*b){free(b->p);memset(b,0,sizeof(*b));}

typedef struct { uint32_t rgb; unsigned char reg; } palent;
static palent stable[240];
static int stable_n=0;

static unsigned char reg_for(uint32_t rgb){
 int i;
 for(i=0;i<stable_n;i++)if(stable[i].rgb==rgb)return stable[i].reg;
 if(stable_n>=240)stable_n=0;
 stable[stable_n].rgb=rgb;stable[stable_n].reg=(unsigned char)(16+stable_n);
 return stable[stable_n++].reg;
}
static int find_color(const uint32_t *colors,int n,uint32_t c){
 int i;for(i=0;i<n;i++)if(colors[i]==c)return i;return -1;
}
static int emit_rle(nd_buf*out,const unsigned char*d,int n){
 int i=0,j,run;char z[32];
 while(i<n){j=i+1;while(j<n&&d[j]==d[i])j++;run=j-i;
  if(run>3){int m=snprintf(z,sizeof(z),"!%d%c",run,d[i]);if(!put(out,z,(size_t)m))return 0;}
  else while(run--)if(!put(out,d+i,1))return 0;
  i=j;
 }return 1;
}
int sixel_encode_rgb(const uint8_t *rgb,int w,int h,nd_buf*out){
 uint32_t colors[240];unsigned char regs[240];int nc=0,x,y,band,ci,first,last;
 int *idx=NULL;unsigned char *line=NULL;char z[96];
 memset(out,0,sizeof(*out));
 idx=(int*)malloc((size_t)w*h*sizeof(int));line=(unsigned char*)malloc((size_t)w);
 if(!idx||!line)goto fail;
 for(y=0;y<h;y++)for(x=0;x<w;x++){
  const uint8_t*p=rgb+3*((size_t)y*w+x);uint32_t c=((uint32_t)p[0]<<16)|((uint32_t)p[1]<<8)|p[2];
  int n=find_color(colors,nc,c);
  if(n<0){if(nc>=240){ /* extremely unlikely for NES nearest-neighbor frames */ n=0; }
   else {n=nc;colors[nc]=c;regs[nc]=reg_for(c);nc++;}}
  idx[(size_t)y*w+x]=n;
 }
 {int n=snprintf(z,sizeof(z),"\x1bP0;1;0q\"1;1;%d;%d",w,h);if(!put(out,z,(size_t)n))goto fail;}
 for(ci=0;ci<nc;ci++){
  uint32_t c=colors[ci];int r=(c>>16)&255,g=(c>>8)&255,b=c&255;
  int n=snprintf(z,sizeof(z),"#%u;2;%d;%d;%d",(unsigned)regs[ci],
   (r*100+127)/255,(g*100+127)/255,(b*100+127)/255);
  if(!put(out,z,(size_t)n))goto fail;
 }
 for(band=0;band<h;band+=6){
  first=1;
  for(ci=0;ci<nc;ci++){
   last=-1;
   for(x=0;x<w;x++){int bits=0;for(y=0;y<6&&band+y<h;y++)if(idx[(size_t)(band+y)*w+x]==ci)bits|=1<<y;if(bits)last=x;}
   if(last<0)continue;
   if(!first&&!putsx(out,"$"))goto fail;first=0;
   {int n=snprintf(z,sizeof(z),"#%u",(unsigned)regs[ci]);if(!put(out,z,(size_t)n))goto fail;}
   for(x=0;x<=last;x++){int bits=0;for(y=0;y<6&&band+y<h;y++)if(idx[(size_t)(band+y)*w+x]==ci)bits|=1<<y;line[x]=(unsigned char)(63+bits);}
   if(!emit_rle(out,line,last+1))goto fail;
  }
  if(!putsx(out,"-"))goto fail;
 }
 if(!putsx(out,"\x1b\\"))goto fail;
 free(idx);free(line);return 1;
fail:
 free(idx);free(line);nd_buf_free(out);return 0;
}
int sixel_encode_scaled(const uint8_t *rgb,int w,int h,int ow,int oh,nd_buf*out){
 uint8_t *tmp;int x,y,ok;
 if(ow==w&&oh==h)return sixel_encode_rgb(rgb,w,h,out);
 tmp=(uint8_t*)malloc((size_t)ow*oh*3);if(!tmp)return 0;
 for(y=0;y<oh;y++){int sy=(int)((long long)y*h/oh);
  for(x=0;x<ow;x++){int sx=(int)((long long)x*w/ow);memcpy(tmp+3*((size_t)y*ow+x),rgb+3*((size_t)sy*w+sx),3);}}
 ok=sixel_encode_rgb(tmp,ow,oh,out);free(tmp);return ok;
}
