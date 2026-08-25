using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Windows.Forms;

internal static class OnePicReleaseSigner
{
    private const string DefaultKeyAlias = "gugugaga2-release";

    [STAThread]
    private static void Main()
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        Application.Run(new SigningForm());
    }

    private sealed class SigningForm : Form
    {
        private readonly string projectRoot;
        private readonly string androidRoot;
        private readonly string buildScript;
        private readonly string keytool;
        private readonly string javaHome;
        private readonly string buildLog;
        private readonly TextBox petIdText;
        private readonly TextBox displayNameText;
        private readonly TextBox applicationIdText;
        private readonly TextBox versionNameText;
        private readonly NumericUpDown versionCodeInput;
        private readonly TextBox pathText;
        private readonly TextBox aliasText;
        private readonly TextBox passwordText;
        private readonly TextBox confirmText;
        private readonly Label statusLabel;
        private readonly Button buildButton;
        private readonly Button cancelButton;
        private string suggestedApplicationId = "com.onepic.desktop.pet";

        internal SigningForm()
        {
            string toolDirectory = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(
                Path.DirectorySeparatorChar,
                Path.AltDirectorySeparatorChar
            );
            projectRoot = Directory.GetParent(toolDirectory).FullName;
            androidRoot = Path.Combine(projectRoot, "android");
            buildScript = Path.Combine(projectRoot, "scripts", "build_android.ps1");
            javaHome = @"C:\Program Files\Android\Android Studio\jbr";
            keytool = Path.Combine(javaHome, "bin", "keytool.exe");
            buildLog = Path.Combine(androidRoot, "release-build.log");

            Text = "OnePic Android Release Signing";
            StartPosition = FormStartPosition.CenterScreen;
            ClientSize = new Size(760, 600);
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            MinimizeBox = false;
            TopMost = true;
            Font = new Font("Segoe UI", 9F);

            Label title = MakeLabel(
                "Build a reusable signed Android pet",
                new Point(24, 16),
                new Size(700, 34)
            );
            title.Font = new Font("Segoe UI", 16F, FontStyle.Bold);
            Controls.Add(title);

            Controls.Add(MakeLabel(
                "Use a unique application ID for each pet. Reuse the same keystore for updates. Passwords are never written to this project.",
                new Point(26, 53),
                new Size(700, 44)
            ));

            Controls.Add(MakeLabel("Pet ID", new Point(26, 105), new Size(130, 24)));
            petIdText = Input(new Point(160, 102), new Size(220, 26), "gugugaga2");
            petIdText.TextChanged += UpdateSuggestedApplicationId;
            Controls.Add(petIdText);

            Controls.Add(MakeLabel("Display name", new Point(402, 105), new Size(110, 24)));
            displayNameText = Input(new Point(514, 102), new Size(220, 26), "OnePic Demo Pet");
            Controls.Add(displayNameText);

            Controls.Add(MakeLabel(
                "Application ID",
                new Point(26, 145),
                new Size(130, 24)
            ));
            applicationIdText = Input(
                new Point(160, 142),
                new Size(574, 26),
                suggestedApplicationId
            );
            Controls.Add(applicationIdText);

            Controls.Add(MakeLabel("Version name", new Point(26, 185), new Size(130, 24)));
            versionNameText = Input(new Point(160, 182), new Size(220, 26), "0.1.0");
            Controls.Add(versionNameText);

            Controls.Add(MakeLabel("Version code", new Point(402, 185), new Size(110, 24)));
            versionCodeInput = new NumericUpDown
            {
                Location = new Point(514, 182),
                Size = new Size(120, 26),
                Minimum = 1,
                Maximum = 2100000000,
                Value = 1
            };
            Controls.Add(versionCodeInput);

            Controls.Add(MakeLabel("Keystore path", new Point(26, 230), new Size(130, 24)));
            pathText = Input(
                new Point(160, 227),
                new Size(474, 26),
                Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments),
                    "AndroidSigning",
                    "gugugaga2-release.jks"
                )
            );
            Controls.Add(pathText);

            Button browseButton = new Button
            {
                Location = new Point(642, 225),
                Size = new Size(92, 30),
                Text = "Browse"
            };
            browseButton.Click += Browse;
            Controls.Add(browseButton);

            Controls.Add(MakeLabel("Key alias", new Point(26, 275), new Size(130, 24)));
            aliasText = Input(new Point(160, 272), new Size(260, 26), DefaultKeyAlias);
            Controls.Add(aliasText);

            Controls.Add(MakeLabel("Password", new Point(26, 320), new Size(130, 24)));
            passwordText = Input(new Point(160, 317), new Size(260, 26), "");
            passwordText.UseSystemPasswordChar = true;
            Controls.Add(passwordText);

            Controls.Add(MakeLabel(
                "Confirm password",
                new Point(26, 365),
                new Size(130, 24)
            ));
            confirmText = Input(new Point(160, 362), new Size(260, 26), "");
            confirmText.UseSystemPasswordChar = true;
            Controls.Add(confirmText);

            statusLabel = MakeLabel(
                "If the .jks exists it will be reused; otherwise a new key will be created. Use at least 12 characters and never send the password in chat.",
                new Point(26, 414),
                new Size(700, 54)
            );
            statusLabel.ForeColor = Color.FromArgb(50, 70, 105);
            Controls.Add(statusLabel);

            buildButton = new Button
            {
                Location = new Point(442, 532),
                Size = new Size(190, 42),
                Text = "Build signed APK"
            };
            buildButton.Click += BuildRelease;
            Controls.Add(buildButton);

            cancelButton = new Button
            {
                Location = new Point(642, 532),
                Size = new Size(92, 42),
                Text = "Cancel"
            };
            cancelButton.Click += delegate
            {
                ClearSecrets();
                Close();
            };
            Controls.Add(cancelButton);

            AcceptButton = buildButton;
            CancelButton = cancelButton;
        }

        private static TextBox Input(Point location, Size size, string text)
        {
            return new TextBox
            {
                Location = location,
                Size = size,
                Text = text
            };
        }

        private static Label MakeLabel(string text, Point location, Size size)
        {
            return new Label
            {
                Text = text,
                Location = location,
                Size = size
            };
        }

        private void UpdateSuggestedApplicationId(object sender, EventArgs eventArgs)
        {
            string current = applicationIdText == null ? "" : applicationIdText.Text.Trim();
            if (applicationIdText != null && current != suggestedApplicationId)
            {
                return;
            }
            suggestedApplicationId = SuggestApplicationId(petIdText.Text.Trim());
            if (applicationIdText != null)
            {
                applicationIdText.Text = suggestedApplicationId;
            }
        }

        private static string SuggestApplicationId(string petId)
        {
            if (string.Equals(petId, "gugugaga2", StringComparison.Ordinal))
            {
                return "com.onepic.desktop.pet";
            }
            string suffix = petId.Replace('-', '_');
            if (Regex.IsMatch(suffix, "^[0-9]"))
            {
                suffix = "pet_" + suffix;
            }
            return "com.onepic.desktop.pet." + suffix;
        }

        private void Browse(object sender, EventArgs eventArgs)
        {
            string initialDirectory = Path.GetDirectoryName(pathText.Text);
            using (SaveFileDialog dialog = new SaveFileDialog())
            {
                dialog.Title = "Choose an existing or new release keystore";
                dialog.Filter = "Java KeyStore (*.jks)|*.jks";
                dialog.FileName = "onepic-release.jks";
                if (Directory.Exists(initialDirectory))
                {
                    dialog.InitialDirectory = initialDirectory;
                }
                if (dialog.ShowDialog(this) == DialogResult.OK)
                {
                    pathText.Text = dialog.FileName;
                }
            }
        }

        private void BuildRelease(object sender, EventArgs eventArgs)
        {
            string petId = petIdText.Text.Trim();
            string displayName = displayNameText.Text.Trim();
            string applicationId = applicationIdText.Text.Trim();
            string versionName = versionNameText.Text.Trim();
            int versionCode = Decimal.ToInt32(versionCodeInput.Value);
            string keyPath = pathText.Text.Trim();
            string keyAlias = aliasText.Text.Trim();
            string password = passwordText.Text;
            string confirmation = confirmText.Text;

            if (!Regex.IsMatch(petId, "^[a-z0-9][a-z0-9_-]*$"))
            {
                ShowWarning("Pet ID must use lowercase letters, digits, hyphens, or underscores.", "Invalid pet ID");
                return;
            }
            if (string.IsNullOrWhiteSpace(displayName) || HasUnsafeArgument(displayName))
            {
                ShowWarning("Choose a display name without quotes or line breaks.", "Invalid display name");
                return;
            }
            if (!Regex.IsMatch(
                applicationId,
                @"^(?:[A-Za-z][A-Za-z0-9_]*\.)+[A-Za-z][A-Za-z0-9_]*$"
            ))
            {
                ShowWarning("Choose a valid, permanent Android application ID.", "Invalid application ID");
                return;
            }
            if (!Regex.IsMatch(versionName, "^[0-9A-Za-z][0-9A-Za-z._-]*$"))
            {
                ShowWarning("Choose a valid version name such as 0.1.0.", "Invalid version");
                return;
            }
            if (string.IsNullOrWhiteSpace(keyPath) || HasUnsafeArgument(keyPath))
            {
                ShowWarning("Choose a valid .jks path.", "Invalid path");
                return;
            }
            if (!keyPath.EndsWith(".jks", StringComparison.OrdinalIgnoreCase))
            {
                ShowWarning("The keystore path must end with .jks.", "Invalid path");
                return;
            }
            if (!Regex.IsMatch(keyAlias, "^[A-Za-z0-9._-]+$"))
            {
                ShowWarning("The key alias contains unsupported characters.", "Invalid alias");
                return;
            }
            if (password.Length < 12)
            {
                ShowWarning("Use a password with at least 12 characters.", "Password too short");
                return;
            }
            if (!string.Equals(password, confirmation, StringComparison.Ordinal))
            {
                ShowWarning("The two passwords do not match.", "Password mismatch");
                return;
            }
            string workspace = Path.Combine(
                projectRoot,
                "user_assets",
                "pets",
                petId,
                "workflow.json"
            );
            if (!File.Exists(workspace))
            {
                ShowWarning("The selected pet workspace does not exist.", "Missing pet");
                return;
            }
            if (!File.Exists(keytool))
            {
                ShowError("Android Studio keytool was not found.", "Missing keytool");
                return;
            }
            if (!File.Exists(buildScript))
            {
                ShowError("The Android release build script was not found.", "Missing build script");
                return;
            }

            buildButton.Enabled = false;
            cancelButton.Enabled = false;
            statusLabel.Text = File.Exists(keyPath)
                ? "Using the existing keystore. Building the signed Release APK..."
                : "Creating the keystore...";
            Refresh();

            try
            {
                if (!File.Exists(keyPath))
                {
                    string keyDirectory = Path.GetDirectoryName(keyPath);
                    if (string.IsNullOrWhiteSpace(keyDirectory))
                    {
                        throw new InvalidOperationException("Choose a keystore directory.");
                    }
                    Directory.CreateDirectory(keyDirectory);
                    RunKeytool(keyPath, keyAlias, password);
                    statusLabel.Text = "Keystore created. Building the signed Release APK...";
                    Refresh();
                }

                RunReleaseBuild(
                    petId,
                    displayName,
                    applicationId,
                    versionName,
                    versionCode,
                    keyPath,
                    keyAlias,
                    password
                );

                string releaseDirectory = Path.Combine(
                    androidRoot,
                    "app",
                    "build",
                    "outputs",
                    "apk",
                    "release"
                );
                string namedApk = Path.Combine(
                    releaseDirectory,
                    petId + "-" + versionName + "-release.apk"
                );
                if (!File.Exists(namedApk))
                {
                    throw new InvalidOperationException(
                        "The build finished without producing the named Release APK."
                    );
                }

                statusLabel.Text = "Release APK created successfully.";
                MessageBox.Show(
                    this,
                    "Success.\r\n\r\nApplication ID:\r\n" + applicationId +
                    "\r\n\r\nKeystore:\r\n" + keyPath +
                    "\r\n\r\nRelease APK:\r\n" + namedApk +
                    "\r\n\r\nBack up the keystore and password.",
                    "Release APK created",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Information
                );
                Close();
            }
            catch (Exception error)
            {
                statusLabel.Text = "Release signing failed.";
                ShowError(error.Message, "Release signing failed");
            }
            finally
            {
                ClearSecrets();
                password = null;
                confirmation = null;
                buildButton.Enabled = true;
                cancelButton.Enabled = true;
            }
        }

        private static bool HasUnsafeArgument(string value)
        {
            return value.IndexOf('"') >= 0
                || value.IndexOf('\r') >= 0
                || value.IndexOf('\n') >= 0;
        }

        private void RunKeytool(string keyPath, string keyAlias, string password)
        {
            ProcessStartInfo info = new ProcessStartInfo
            {
                FileName = keytool,
                Arguments =
                    "-genkeypair -v -keystore " + Quote(keyPath) +
                    " -storetype PKCS12" +
                    " -storepass:env ONEPIC_RELEASE_PASSWORD" +
                    " -keypass:env ONEPIC_RELEASE_PASSWORD" +
                    " -alias " + Quote(keyAlias) +
                    " -keyalg RSA -keysize 4096 -validity 10000" +
                    " -dname " + Quote(
                        "CN=OnePic Desktop Pet, OU=Release, O=OnePic, L=Local, ST=Local, C=CN"
                    ),
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true
            };
            info.EnvironmentVariables["ONEPIC_RELEASE_PASSWORD"] = password;

            ProcessResult result = RunProcess(info);
            if (result.ExitCode != 0 || !File.Exists(keyPath))
            {
                throw new InvalidOperationException(
                    "keytool failed.\r\n\r\n" + result.Output + "\r\n" + result.Error
                );
            }
        }

        private void RunReleaseBuild(
            string petId,
            string displayName,
            string applicationId,
            string versionName,
            int versionCode,
            string keyPath,
            string keyAlias,
            string password
        )
        {
            ProcessStartInfo info = new ProcessStartInfo
            {
                FileName = "powershell.exe",
                Arguments =
                    "-NoProfile -ExecutionPolicy Bypass -File " + Quote(buildScript) +
                    " -PetId " + Quote(petId) +
                    " -Configuration Release" +
                    " -DisplayName " + Quote(displayName) +
                    " -ApplicationId " + Quote(applicationId) +
                    " -VersionCode " + versionCode +
                    " -VersionName " + Quote(versionName),
                WorkingDirectory = projectRoot,
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true
            };
            info.EnvironmentVariables["JAVA_HOME"] = javaHome;
            info.EnvironmentVariables["ONEPIC_ANDROID_KEYSTORE"] = keyPath;
            info.EnvironmentVariables["ONEPIC_ANDROID_KEY_ALIAS"] = keyAlias;
            info.EnvironmentVariables["ONEPIC_ANDROID_KEYSTORE_PASSWORD"] = password;
            info.EnvironmentVariables["ONEPIC_ANDROID_KEY_PASSWORD"] = password;

            ProcessResult result = RunProcess(info);
            File.WriteAllText(
                buildLog,
                result.Output + Environment.NewLine + result.Error,
                new UTF8Encoding(false)
            );
            if (result.ExitCode != 0)
            {
                throw new InvalidOperationException(
                    "Release build failed. See:\r\n" + buildLog
                );
            }
        }

        private static ProcessResult RunProcess(ProcessStartInfo info)
        {
            using (Process process = new Process())
            {
                process.StartInfo = info;
                process.Start();
                Task<string> outputTask = process.StandardOutput.ReadToEndAsync();
                Task<string> errorTask = process.StandardError.ReadToEndAsync();
                process.WaitForExit();
                Task.WaitAll(outputTask, errorTask);
                return new ProcessResult(
                    process.ExitCode,
                    outputTask.Result,
                    errorTask.Result
                );
            }
        }

        private static string Quote(string value)
        {
            return "\"" + value.Replace("\"", "\\\"") + "\"";
        }

        private void ClearSecrets()
        {
            passwordText.Clear();
            confirmText.Clear();
        }

        private void ShowWarning(string message, string title)
        {
            MessageBox.Show(
                this,
                message,
                title,
                MessageBoxButtons.OK,
                MessageBoxIcon.Warning
            );
        }

        private void ShowError(string message, string title)
        {
            MessageBox.Show(
                this,
                message,
                title,
                MessageBoxButtons.OK,
                MessageBoxIcon.Error
            );
        }
    }

    private sealed class ProcessResult
    {
        internal ProcessResult(int exitCode, string output, string error)
        {
            ExitCode = exitCode;
            Output = output;
            Error = error;
        }

        internal int ExitCode { get; private set; }
        internal string Output { get; private set; }
        internal string Error { get; private set; }
    }
}
