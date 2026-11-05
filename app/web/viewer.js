/* Local, dependency-free display of the private review tessellation. */
"use strict";
globalThis.renderReviewMesh = async function (canvas, url) {
  const response = await fetch(url);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "The review mesh could not be loaded");
  if (!canvas.isConnected) throw new Error("The preview page has changed");
  const raw = data.triangles;
  if (!Array.isArray(raw) || raw.length < 9 || raw.length % 9 || raw.length > 2250000 || !raw.every(Number.isFinite)) throw new Error("Invalid review mesh");
  const gl = canvas.getContext("webgl", {antialias:true, alpha:false});
  if (!gl) throw new Error("Interactive 3D is unavailable in this browser. Use the saved preview and inspect the model in SolidWorks.");
  const lo = [Infinity,Infinity,Infinity], hi = [-Infinity,-Infinity,-Infinity];
  for (let i=0;i<raw.length;i++) { const a=i%3; lo[a]=Math.min(lo[a],raw[i]); hi[a]=Math.max(hi[a],raw[i]); }
  const span = Math.max(...hi.map((v,i) => v-lo[i]));
  if (!(span > 0)) throw new Error("The review mesh is empty");
  const vertices = new Float32Array(raw.length), normals = new Float32Array(raw.length);
  for (let i=0;i<raw.length;i++) vertices[i]=(raw[i]-(lo[i%3]+hi[i%3])/2)/span;
  for (let i=0;i<raw.length;i+=9) {
    const a=[0,1,2].map((j) => vertices[i+3+j]-vertices[i+j]);
    const b=[0,1,2].map((j) => vertices[i+6+j]-vertices[i+j]);
    const n=[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
    const size=Math.hypot(...n) || 1;
    for (let v=0;v<3;v++) for (let j=0;j<3;j++) normals[i+v*3+j]=n[j]/size;
  }
  function shader(type, text) {
    const s=gl.createShader(type); gl.shaderSource(s,text); gl.compileShader(s);
    if (!gl.getShaderParameter(s,gl.COMPILE_STATUS)) throw new Error("The 3D renderer could not initialize");
    return s;
  }
  const program=gl.createProgram();
  gl.attachShader(program,shader(gl.VERTEX_SHADER,"attribute vec3 p,n; uniform mat3 rotation; uniform vec2 fit; varying vec3 normal; void main(){vec3 v=rotation*p; normal=rotation*n; gl_Position=vec4(v.x*fit.x,v.y*fit.y,-v.z*.5,1.0);}"));
  gl.attachShader(program,shader(gl.FRAGMENT_SHADER,"precision mediump float; varying vec3 normal; void main(){float light=.35+.65*abs(dot(normalize(normal),normalize(vec3(.4,.6,1.0)))); gl_FragColor=vec4(vec3(.38,.61,.84)*light,1.0);}"));
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program,gl.LINK_STATUS)) throw new Error("The 3D renderer could not initialize");
  gl.useProgram(program);
  for (const [name, values] of [["p",vertices],["n",normals]]) {
    gl.bindBuffer(gl.ARRAY_BUFFER,gl.createBuffer()); gl.bufferData(gl.ARRAY_BUFFER,values,gl.STATIC_DRAW);
    const location=gl.getAttribLocation(program,name); gl.enableVertexAttribArray(location); gl.vertexAttribPointer(location,3,gl.FLOAT,false,0,0);
  }
  const rotation=gl.getUniformLocation(program,"rotation"), fit=gl.getUniformLocation(program,"fit");
  let yaw=-.7, pitch=.5, zoom=1.25, drag=null, disposed=false;
  function draw() {
    if (disposed || !canvas.isConnected) return;
    const ratio=Math.min(devicePixelRatio || 1,2);
    canvas.width=Math.max(1,Math.round(canvas.clientWidth*ratio)); canvas.height=Math.max(1,Math.round(canvas.clientHeight*ratio));
    gl.viewport(0,0,canvas.width,canvas.height); gl.clearColor(.94,.96,.98,1); gl.enable(gl.DEPTH_TEST); gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);
    const c=Math.cos(yaw), s=Math.sin(yaw), cp=Math.cos(pitch), sp=Math.sin(pitch);
    // Yaw around model Z, then pitch around camera X. Z remains the up axis.
    gl.uniformMatrix3fv(rotation,false,new Float32Array([c,cp*s,sp*s, -s,cp*c,sp*c, 0,-sp,cp]));
    const aspect=canvas.width/canvas.height;
    gl.uniform2f(fit,zoom/Math.max(1,aspect),zoom*Math.min(1,aspect)); gl.drawArrays(gl.TRIANGLES,0,vertices.length/3);
  }
  canvas.addEventListener("pointerdown",(event) => {drag=[event.clientX,event.clientY];canvas.setPointerCapture(event.pointerId);});
  canvas.addEventListener("pointerup",() => {drag=null;});
  canvas.addEventListener("pointercancel",() => {drag=null;});
  canvas.addEventListener("pointermove",(event) => {if(drag){yaw+=(event.clientX-drag[0])*.01;pitch+=(event.clientY-drag[1])*.01;drag=[event.clientX,event.clientY];draw();}});
  canvas.addEventListener("wheel",(event) => {event.preventDefault();zoom=Math.max(.4,Math.min(6,zoom*Math.exp(-event.deltaY*.001)));draw();},{passive:false});
  canvas.addEventListener("keydown",(event) => {
    if (["ArrowLeft","ArrowRight","ArrowUp","ArrowDown","+","-"].includes(event.key)) event.preventDefault();
    if(event.key==="ArrowLeft")yaw-=.1; if(event.key==="ArrowRight")yaw+=.1; if(event.key==="ArrowUp")pitch-=.1; if(event.key==="ArrowDown")pitch+=.1;
    if(event.key==="+")zoom=Math.min(6,zoom*1.1); if(event.key==="-")zoom=Math.max(.4,zoom/1.1); draw();
  });
  draw();
  const resize = typeof ResizeObserver === "function" ? new ResizeObserver(draw) : null;
  resize?.observe(canvas);
  return {dispose() { disposed=true; resize?.disconnect(); gl.getExtension("WEBGL_lose_context")?.loseContext(); }, view(name) { [yaw,pitch]=({Isometric:[-.7,.8],Front:[0,Math.PI/2],Top:[0,0],Right:[-Math.PI/2,Math.PI/2]})[name] || [-.7,.8]; zoom=1.25;draw(); }};
};
