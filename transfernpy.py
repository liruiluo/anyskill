import numpy as np
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D

# 模拟数据（你需要替换为你实际的数据）
root_translation = np.array([[1.703236, -1.642042, 0.78390056], [1.6842335, -1.6219158, 0.7852436], ...])  # 替换为你的数据

# 创建一个3D图形
fig = plt.figure()
ax = fig.add_subplot(111, projection='3d')

# 提取X, Y, Z轴数据
x = root_translation[:, 0]
y = root_translation[:, 1]
z = root_translation[:, 2]

# 绘制根平移轨迹
ax.plot(x, y, z, label='Root Translation Trajectory', color='b')

# 设置图例和标签
ax.set_xlabel('X axis')
ax.set_ylabel('Y axis')
ax.set_zlabel('Z axis')
ax.legend()

plt.show()
