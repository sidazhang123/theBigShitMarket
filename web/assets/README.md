# 应用图标

`app-icon.png` 是以现有 `poop_rgba.png` 为参考、使用内置 imagegen 编辑生成的透明图标：保留三层造型、卷曲尖顶与棕金配色，去除底部投影，增强轮廓对比，简化小尺寸细节。原有背景图保持不变。

`app.ico` 由该 PNG 通过 Pillow 编码，包含 16、20、24、32、40、48、64、128、256 像素版本。EXE 资源和原生窗口共享这一文件，随 `web` 目录打包。

生成提示词：

```text
Use case: precise-object-edit. Asset type: Windows desktop application icon, square canvas with true transparent background. Edit target: attached existing app background poop illustration. Preserve its recognizable three stacked coil silhouette, curled pointed top, warm ochre brown/gold palette and simple smooth illustrated shading. Remove the large oval gray ground shadow completely. Increase silhouette contrast and simplify tiny internal spots so the mark is readable at 16x16 and 32x32 pixels. One single centered poop coil, large on canvas with small even transparent padding, no eyes, no face, no text, no frame, no background tile, no ground shadow. Clean smooth antialiased edge, opaque poop body. This is the existing application's mark adapted for an icon, keep its visual identity.
```

重新编码 ICO（在项目根目录执行）：

```text
py -c "from PIL import Image; Image.open('web/assets/app-icon.png').save('web/assets/app.ico', format='ICO', sizes=[(s,s) for s in (16,20,24,32,40,48,64,128,256)])"
```
