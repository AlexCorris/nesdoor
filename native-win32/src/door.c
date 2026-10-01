#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <windows.h>
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#include <stdlib.h>
#include "door.h"

static SOCKET g_sock = INVALID_SOCKET;
static char g_alias[80] = "Player";
static int g_wsa = 0;

static void trim(char *s) {
    size_t n = strlen(s);
    while(n && (s[n-1]=='\r'||s[n-1]=='\n'||s[n-1]==' '||s[n-1]=='\t')) s[--n]=0;
}
static int make_path(char *out, size_t cap, const char *arg) {
    DWORD a;
    if(!arg || !*arg) return 0;
    a = GetFileAttributesA(arg);
    if(a != INVALID_FILE_ATTRIBUTES && (a & FILE_ATTRIBUTE_DIRECTORY)) {
        snprintf(out, cap, "%s%sDOOR32.SYS", arg,
                 (arg[strlen(arg)-1]=='\\'||arg[strlen(arg)-1]=='/') ? "" : "\\");
        return 1;
    }
    strncpy(out,arg,cap-1); out[cap-1]=0; return 1;
}
int door_open(const char *arg) {
    char path[MAX_PATH], line[512];
    FILE *f;
    unsigned long long h = 0;
    int i;
    WSADATA wd;
    if(!make_path(path,sizeof(path),arg)) return 0;
    f=fopen(path,"rb");
    if(!f) return 0;
    for(i=1;i<=7;i++) {
        if(!fgets(line,sizeof(line),f)) { fclose(f); return 0; }
        trim(line);
        if(i==2) h=_strtoui64(line,NULL,10);
        if(i==7 && *line) { strncpy(g_alias,line,sizeof(g_alias)-1); g_alias[sizeof(g_alias)-1]=0; }
    }
    fclose(f);
    if(WSAStartup(MAKEWORD(2,2),&wd)!=0) return 0;
    g_wsa=1;
    g_sock=(SOCKET)(uintptr_t)h;
    if(g_sock==INVALID_SOCKET) return 0;
    {
        u_long nb=1;
        ioctlsocket(g_sock,FIONBIO,&nb);
    }
    return 1;
}
void door_close(void) {
    /* Synchronet owns the inherited socket.  Do not close or shut it down.
       Also leave Winsock process cleanup to normal process teardown so the
       parent-owned inherited connection is not disturbed on door return. */
    g_sock=INVALID_SOCKET;
    g_wsa=0;
}
int door_connected(void) { return g_sock!=INVALID_SOCKET; }
const char *door_alias(void) { return g_alias; }
int door_write(const void *buf,size_t len) {
    const char *p=(const char*)buf;
    while(len) {
        int n=send(g_sock,p,(int)(len>32767?32767:len),0);
        if(n>0) { p+=n; len-=n; continue; }
        if(WSAGetLastError()==WSAEWOULDBLOCK) { Sleep(1); continue; }
        return 0;
    }
    return 1;
}
int door_printf(const char *fmt,...) {
    char b[8192]; va_list ap; int n;
    va_start(ap,fmt); n=_vsnprintf(b,sizeof(b)-1,fmt,ap); va_end(ap);
    if(n<0) n=(int)strlen(b);
    b[sizeof(b)-1]=0;
    return door_write(b,(size_t)n);
}
int door_read(unsigned char *buf,size_t maxlen,int timeout_ms) {
    fd_set r; struct timeval tv; int rc;
    if(g_sock==INVALID_SOCKET) return -1;
    FD_ZERO(&r); FD_SET(g_sock,&r);
    tv.tv_sec=timeout_ms/1000; tv.tv_usec=(timeout_ms%1000)*1000;
    rc=select(0,&r,NULL,NULL,&tv);
    if(rc==0) return 0;
    if(rc<0) return -1;
    rc=recv(g_sock,(char*)buf,(int)maxlen,0);
    if(rc==0) return -1;
    if(rc<0 && WSAGetLastError()==WSAEWOULDBLOCK) return 0;
    return rc;
}
