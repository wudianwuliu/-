把要上传的文件放这个目录，然后在 profile.yaml 里写相对路径，例如：

    basic:
      photo: assets/证件照.jpg      # 一寸/两寸证件照，多数网申要求 jpg、小于 1MB

也可以在填表时用命令行参数临时指定，不写进 profile：

    py -3 -m jobfill fill --resume-file "D:\简历\我的简历.pdf" --photo-file "D:\照片\证件照.jpg"

说明：
  * 简历附件、证件照这类控件没有 profile 数据可依赖，工具靠**标签文字**判断该传哪个文件：
      含「简历/附件/resume」→ --resume-file
      含「照片/证件照/相片/头像」→ --photo-file 或 profile 里的 basic.photo
  * 文件不存在时不会瞎传，会标成「需手动上传」并写进报告。
  * 没配置的附件控件一律留空，不会乱传文件。
