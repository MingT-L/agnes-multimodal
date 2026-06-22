# 高质量 Prompt 模板库

把用户的口语化描述改写成高密度 prompt 时，按下面的要素检查：

1. **主体**（subject）：明确的对象（人 / 动物 / 物品 / 场景）
2. **动作 / 状态**（action / state）：正在做什么
3. **环境**（setting）：地点、时间、天气
4. **风格**（style）：写实、插画、水彩、3D、电影感、动漫
5. **光线**（lighting）：暖光、侧光、霓虹、柔光、自然光
6. **构图 / 镜头**（composition）：广角、特写、俯拍、平视、景深
7. **画质**（quality）：high detail, 8k, sharp, masterpiece

---

## 一、文生图（`agnes-image-2.1-flash`）

### 1.1 角色 / IP 形象

**用户口语**：「画一个赛博朋克少女」
**改写**：
```
A young woman with neon-streaked hair, wearing a reflective cyber jacket, 
standing in a rainy neon-lit Tokyo alley at night, cinematic lighting, 
cyberpunk 2077 style, ultra detailed, 8k, sharp focus
```

### 1.2 产品 / 电商主图

**用户口语**：「做一个无线耳机的产品图」
**改写**：
```
Premium wireless earbuds floating on a soft gradient background, 
studio lighting, soft shadow, glossy plastic and metal texture, 
clean product photography, commercial style, 8k, sharp, white background
```

### 1.3 海报 / Banner

**用户口语**：「618 大促海报」
**改写**：
```
Bold promotional poster design for 618 shopping festival, 
dynamic red and gold color scheme, large discount text "618" 
in the center, confetti and light rays radiating, modern flat design, 
commercial poster style, ultra detailed
```

### 1.4 风景

**用户口语**：「秋天九寨沟」
**改写**：
```
Autumn scenery of Jiuzhaigou Valley, multi-colored forests in red orange 
and gold, crystal clear turquoise lake reflecting the trees, 
morning mist, soft sunlight, national geographic style, 
landscape photography, 8k, high detail
```

### 1.5 二次元 / 插画

**用户口语**：「动漫风格的小女孩」
**改写**：
```
Anime style illustration of a 10-year-old girl with twintails, 
wearing a school uniform, holding a cat, soft pastel color palette, 
cherry blossom background, Studio Ghibli inspired, 
high detail, clean lineart, 4k
```

---

## 二、图生图 / 编辑（`agnes-image-2.0-flash`）

### 2.1 风格转换

**用户口语**：「改成水彩风格」
**改写**：
```
Keep the same subject, composition, and colors. Change the rendering 
style to watercolor painting, soft brush strokes, paper texture, 
color bleeding, traditional art style
```

### 2.2 场景替换

**用户口语**：「把白天换成夜景」
**改写**：
```
Keep the same subject, pose, and composition. Change the time of day 
to night, with neon city lights in the background, cinematic blue tone, 
rim lighting on the subject
```

### 2.3 局部编辑

**用户口语**：「把人物的衣服换成红色」
**改写**：
```
Keep the scene, background, pose, and composition unchanged. 
Change the character's clothing color to red, keep the fabric 
material and texture consistent
```

### 2.4 去除/增加元素

**用户口语**：「去掉背景里的人物」
**改写**：
```
Keep the main subject unchanged. Remove all background people 
and clutter, keep the environment architecture and lighting, 
clean background
```

---

## 三、文生视频（`agnes-video-v2.0`）

### 3.1 镜头语言公式

视频 prompt 重点描述**镜头运动**和**时间维度**：

```
[主体动作] + [环境变化] + [镜头运动] + [风格] + [时长意图] + [fps/24]
```

### 3.2 风景

**用户口语**：「一段 10 秒的海边日落」
**改写**：
```
Cinematic sunset over a calm ocean, golden light reflecting on water 
ripples, slow dolly forward, a few seabirds flying across the frame, 
gentle waves lapping the shore, warm color grading, 24fps, smooth motion, 
10 seconds, photorealistic, 4k
```

### 3.3 人物 / 角色

**用户口语**：「一个女孩在咖啡馆看书」
**改写**：
```
A young woman sitting by a window in a cozy cafe, reading a book, 
soft afternoon light coming through the window, steam rising from 
her coffee cup, slow subtle camera push-in, ambient atmosphere, 
24fps, 10 seconds, cinematic, warm tone
```

### 3.4 商业广告

**用户口语**：「产品广告视频」
**改写**：
```
A premium perfume bottle rotating slowly on a marble pedestal, 
soft studio lighting, particles of light floating around, 
lens flare, smooth orbital camera movement, luxury commercial style, 
5 seconds, 24fps, 4k, high production value
```

### 3.5 抽象 / 艺术

**用户口语**：「一段抽象艺术视频」
**改写**：
```
Abstract fluid art, iridescent paint mixing and flowing in slow motion, 
macro lens, vibrant neon colors blending, smooth continuous motion, 
no hard cuts, generative art style, 10 seconds, 24fps, 4k
```

---

## 四、避坑清单

- ❌ **不要**写「最好的、最漂亮的、完美的」等空泛形容词
- ❌ **不要**超过 200 词（信息密度过高反而失焦）
- ❌ **不要**用否定句（「不要出现人物」），改用正向描述（「空旷无人的街道」）
- ❌ **不要**对视频 prompt 写大量静态描述（视频需要运动和变化）
- ✅ **要**指定光线（lighting 是出图质量的决定因素）
- ✅ **要**指定画质后缀（`8k`, `high detail`, `masterpiece`）
- ✅ **要**为视频 prompt 写明镜头运动（dolly / pan / zoom / static）
- ✅ **要**保持主体一致性（图生图必须显式说「Keep the subject」）
