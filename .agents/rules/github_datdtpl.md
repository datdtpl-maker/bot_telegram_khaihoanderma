# GitHub Configuration for datdtpl-maker

- Tài khoản GitHub: https://github.com/datdtpl-maker
- Toàn bộ thao tác `git push`, `git fetch`, `git clone` tới các repositories của tài khoản `datdtpl-maker` đã được cấu hình tự động xác thực bằng Personal Access Token (PAT) trong Git toàn cục trên máy tính này (`git config --global url...insteadOf`).
- Khi người dùng yêu cầu push / publish code lên repository của `datdtpl-maker`, agent chỉ cần thực thi trực tiếp lệnh `git push origin <branch>` mà không cần yêu cầu người dùng cung cấp lại token.
