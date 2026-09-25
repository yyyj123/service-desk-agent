from starlette.responses import JSONResponse

class BodyLimitMiddleware:
    """Bound memory before JSON parsing, including requests with chunked bodies."""
    def __init__(self,app,limit=65536):
        self.app,self.limit=app,limit

    async def __call__(self,scope,receive,send):
        if scope['type']!='http':
            return await self.app(scope,receive,send)
        size=0
        limit=1500000 if scope.get('path','').startswith('/api/admin/knowledge') else self.limit
        chunks=[]
        while True:
            message=await receive()
            if message['type']=='http.disconnect':
                return
            size+=len(message.get('body',b''))
            if size>limit:
                return await JSONResponse({'detail':'请求体过大'},status_code=413)(scope,receive,send)
            chunks.append(message)
            if not message.get('more_body',False):
                break
        cursor=iter(chunks)
        async def buffered():
            try:
                return next(cursor)
            except StopIteration:
                return await receive()
        await self.app(scope,buffered,send)
